#!/usr/bin/env python3
"""localize.py - translate captions and build multi-language versions.

Inspired by VideoLingo's translation stage, but keeps the skill's rule:
**free and local only** - no paid API keys anywhere.

Backends (auto-detected, first available wins, override with --backend):
  argos      pip install argostranslate      offline neural MT, fully free
  ollama     `ollama serve` on localhost     free local LLM (llama3, qwen...)
  libre      LibreTranslate instance         free/self-hosted, set
                                             LIBRETRANSLATE_URL (default
                                             http://localhost:5000)

A glossary keeps your brand names, product names and slang intact - the
single biggest quality win when translating ad copy.

Usage:
  # translate a words JSON (keeps original word timings)
  python3 localize.py translate --words words.json --to en --out words_en.json

  # translate an SRT
  python3 localize.py translate --srt subs.srt --to es --out subs_es.srt

  # dual-language captions (original on top, translation below)
  python3 localize.py dual --words words.json --to en --out dual.ass

  # burn a translated caption track straight onto a video
  python3 localize.py burn --video cut.mp4 --words words.json --to en \\
      --style tiktok --out cut_en.mp4

  python3 localize.py backends       # what is installed right now

Glossary file: JSON {"NeuroFlow": "NeuroFlow", "подпишись": "subscribe"}
Terms are protected during translation and restored afterwards.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops        # noqa: E402
import captions   # noqa: E402

LIBRE_URL = os.environ.get("LIBRETRANSLATE_URL", "http://localhost:5000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")


# --------------------------------------------------------------------------
# backend discovery
# --------------------------------------------------------------------------
def _have_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _http_alive(url, path="/"):
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=1.5) as r:
            return r.status < 500
    except Exception:
        return False


def available_backends():
    found = []
    if _have_module("argostranslate"):
        found.append("argos")
    if _http_alive(OLLAMA_URL, "/api/tags"):
        found.append("ollama")
    if _http_alive(LIBRE_URL, "/languages"):
        found.append("libre")
    return found


# --------------------------------------------------------------------------
# glossary protection
# --------------------------------------------------------------------------
def _protect(text, glossary):
    """Replace glossary terms with placeholders the MT engine will not touch."""
    mapping = {}
    for i, (src, dst) in enumerate(sorted(glossary.items(),
                                          key=lambda kv: -len(kv[0]))):
        token = f"█{i}█"
        pattern = re.compile(re.escape(src), re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub(token, text)
            mapping[token] = dst
    return text, mapping


def _restore(text, mapping):
    for token, dst in mapping.items():
        text = text.replace(token, dst)
    return text


# --------------------------------------------------------------------------
# translation backends
# --------------------------------------------------------------------------
def _tr_argos(chunks, src, dst):
    import argostranslate.package as pkg      # noqa: WPS433
    import argostranslate.translate as tr     # noqa: WPS433
    if src in (None, "auto"):
        raise ops.InputError("argos needs an explicit --from language code")
    installed = tr.get_installed_languages()
    from_lang = next((l for l in installed if l.code == src), None)
    to_lang = next((l for l in installed if l.code == dst), None)
    if not (from_lang and to_lang):
        try:
            pkg.update_package_index()
            match = next(p for p in pkg.get_available_packages()
                         if p.from_code == src and p.to_code == dst)
            pkg.install_from_path(match.download())
            installed = tr.get_installed_languages()
            from_lang = next(l for l in installed if l.code == src)
            to_lang = next(l for l in installed if l.code == dst)
        except Exception as exc:
            raise ops.InputError(
                f"argos has no {src}->{dst} model and could not download one: {exc}")
    engine = from_lang.get_translation(to_lang)
    return [engine.translate(c) for c in chunks]


def _post_json(url, payload, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _tr_libre(chunks, src, dst):
    out = []
    for c in chunks:
        res = _post_json(LIBRE_URL.rstrip("/") + "/translate",
                         {"q": c, "source": src or "auto", "target": dst,
                          "format": "text"})
        out.append(res.get("translatedText", c))
    return out


def _tr_ollama(chunks, src, dst, tone="natural spoken social-media copy"):
    """Translate-and-adapt in one shot with a local LLM.

    Line-numbered batches keep the mapping 1:1 even if the model rambles.
    """
    out = []
    batch = 20
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        numbered = "\n".join(f"{j + 1}. {t}" for j, t in enumerate(part))
        prompt = (
            f"Translate each numbered line from {src or 'the source language'} "
            f"to {dst}. Style: {tone}. Keep it short enough to fit the same "
            "screen time, keep any █N█ placeholders untouched, keep the "
            "numbering, and output nothing else.\n\n" + numbered)
        res = _post_json(OLLAMA_URL.rstrip("/") + "/api/generate",
                         {"model": OLLAMA_MODEL, "prompt": prompt,
                          "stream": False, "options": {"temperature": 0.3}})
        text = res.get("response", "")
        got = {}
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if m:
                got[int(m.group(1))] = m.group(2).strip()
        for j, original in enumerate(part):
            out.append(got.get(j + 1, original))
    return out


_ENGINES = {"argos": _tr_argos, "libre": _tr_libre, "ollama": _tr_ollama}


def translate_texts(texts, to_lang, from_lang=None, backend=None, glossary=None):
    found = available_backends()
    backend = backend or (found[0] if found else None)
    if not backend:
        raise ops.InputError(
            "no free translation backend available. Pick one:\n"
            "  pip install argostranslate          (offline, no server)\n"
            "  ollama serve + ollama pull llama3.1 (local LLM, best quality)\n"
            "  docker run -p 5000:5000 libretranslate/libretranslate\n"
            "Then re-run. All three are free.")
    glossary = glossary or {}
    protected, maps = [], []
    for t in texts:
        p, m = _protect(t, glossary)
        protected.append(p)
        maps.append(m)
    translated = _ENGINES[backend](protected, from_lang, to_lang)
    return [_restore(t, m) for t, m in zip(translated, maps)], backend


# --------------------------------------------------------------------------
# caption-level helpers
# --------------------------------------------------------------------------
def _lines_from_words(words, per_line=3, max_chars=24, max_cps=20.0):
    groups = captions.group_words(words, per_line=per_line, max_chars=max_chars)
    groups = captions.enforce_readability(groups, max_chars=max_chars,
                                          max_cps=max_cps)
    return [{"text": " ".join(str(x["text"]) for x in g),
             "start": float(g[0]["start"]), "end": float(g[-1]["end"])}
            for g in groups]


def _redistribute(segment, translated_text):
    """Spread a translated line back over its time span, word by word."""
    parts = translated_text.split()
    if not parts:
        return []
    span = max(0.05, segment["end"] - segment["start"])
    weights = [len(p) + 1 for p in parts]
    total = float(sum(weights))
    out, t = [], segment["start"]
    for p, wgt in zip(parts, weights):
        dur = span * (wgt / total)
        out.append({"text": p, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    return out


def load_source(words_path=None, srt_path=None):
    if words_path:
        with open(words_path, encoding="utf-8") as fh:
            return json.load(fh)
    if srt_path:
        return captions.parse_srt(srt_path)
    raise ops.InputError("pass --words words.json or --srt subs.srt")


def _srt_time(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments, path, second_line=None):
    blocks = []
    for i, seg in enumerate(segments, 1):
        text = seg["text"]
        if second_line:
            text = f"{second_line[i - 1]}\n{text}"
        blocks.append(f"{i}\n{_srt_time(seg['start'])} --> "
                      f"{_srt_time(seg['end'])}\n{text}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(blocks))
    return path


def translate_captions(words, to_lang, from_lang=None, backend=None,
                       glossary=None, per_line=3, max_chars=24):
    """Returns (translated_words, segments, translated_texts, backend)."""
    segments = _lines_from_words(words, per_line=per_line, max_chars=max_chars)
    texts, used = translate_texts([s["text"] for s in segments], to_lang,
                                  from_lang, backend, glossary)
    new_words = []
    for seg, txt in zip(segments, texts):
        new_words.extend(_redistribute(seg, txt))
    return new_words, segments, texts, used


def build_dual_ass(segments, translated, w=1080, h=1920, fontname="DejaVu Sans",
                   fontsize=None, safe="reels", accent="#FFFFFF"):
    """Original above, translation below - the VideoLingo bilingual layout."""
    fontsize = fontsize or int(h * 0.040)
    mv = int(h * captions.SAFE_ZONES.get(safe, captions.SAFE_ZONES["reels"]))
    header = captions._header(
        w, h, fontsize, captions.WHITE, captions.WHITE,
        ops._ass_color("#101010"), ops._ass_color("black@0.35"), 2, mv,
        outline=max(3, int(fontsize * 0.08)), fontname=fontname)
    body = ""
    small = int(fontsize * 0.78)
    for seg, tr in zip(segments, translated):
        text = (f"{{\\fs{fontsize}\\b1}}{tr}{{\\r}}\\N"
                f"{{\\fs{small}\\alpha&H40&}}{seg['text']}")
        body += (f"Dialogue: 0,{ops._ass_time(seg['start'])},"
                 f"{ops._ass_time(seg['end'])},Cap,,0,0,0,,{text}\n")
    f = tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False,
                                    encoding="utf-8")
    f.write(header + body)
    f.close()
    return f.name


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _glossary(path):
    if not path:
        return {}
    ops.require_file(path, "glossary JSON")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_parser():
    p = argparse.ArgumentParser(description="Free/local caption translation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backends", help="list available free translation engines")

    for name in ("translate", "dual", "burn"):
        s = sub.add_parser(name)
        s.add_argument("--words")
        s.add_argument("--srt")
        s.add_argument("--to", dest="to_lang", required=True,
                       help="target language code, e.g. en / es / de")
        s.add_argument("--from", dest="from_lang",
                       help="source language code (required for argos)")
        s.add_argument("--backend", choices=sorted(_ENGINES))
        s.add_argument("--glossary", help="JSON of terms to keep / force")
        s.add_argument("--per-line", type=int, default=3)
        s.add_argument("--max-chars", type=int, default=24)
        s.add_argument("--width", type=int, default=1080)
        s.add_argument("--height", type=int, default=1920)
        s.add_argument("--safe", default="reels", choices=sorted(captions.SAFE_ZONES))
        s.add_argument("--fontname", default="DejaVu Sans")
        s.add_argument("--fontsize", type=int)
        s.add_argument("--out", required=True)
        if name == "burn":
            s.add_argument("--video", required=True)
            s.add_argument("--style", default="tiktok",
                           choices=["tiktok", "pop", "karaoke", "box", "clean", "dual"])
            s.add_argument("--accent", default="#00E5FF")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)

    if a.cmd == "backends":
        print(json.dumps({"ok": True, "available": available_backends(),
                          "libretranslate_url": LIBRE_URL,
                          "ollama_url": OLLAMA_URL,
                          "ollama_model": OLLAMA_MODEL}, indent=2))
        return

    words = load_source(a.words, a.srt)
    new_words, segments, texts, backend = translate_captions(
        words, a.to_lang, a.from_lang, a.backend, _glossary(a.glossary),
        a.per_line, a.max_chars)

    if a.cmd == "translate":
        if a.out.lower().endswith(".srt"):
            trans_segments = [dict(s, text=t) for s, t in zip(segments, texts)]
            write_srt(trans_segments, a.out)
        else:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(new_words, fh, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "backend": backend, "output": a.out,
                          "segments": len(segments), "words": len(new_words),
                          "preview": texts[:3]}, ensure_ascii=False, indent=2))
        return

    if a.cmd == "dual":
        path = build_dual_ass(segments, texts, a.width, a.height, a.fontname,
                              a.fontsize, a.safe)
        os.replace(path, a.out)
        print(json.dumps({"ok": True, "backend": backend, "ass": a.out,
                          "segments": len(segments)}, ensure_ascii=False))
        return

    # burn
    w, h = ops._dims(a.video)
    w, h = w or a.width, h or a.height
    if a.style == "dual":
        ass_path = build_dual_ass(segments, texts, w, h, a.fontname,
                                  a.fontsize, a.safe)
    else:
        ass_path = captions.build_ass(new_words, w=w, h=h, style=a.style,
                                      accent=a.accent, per_line=a.per_line,
                                      max_chars=a.max_chars, safe=a.safe,
                                      fontname=a.fontname, fontsize=a.fontsize)
    ops.burn_subtitles(a.video, ass_path, a.out)
    os.unlink(ass_path)
    print(json.dumps({"ok": True, "backend": backend, "output": a.out,
                      "language": a.to_lang,
                      "duration_sec": round(ops._dur(a.out), 3)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
