#!/usr/bin/env python3
"""dub.py - free, local text-to-speech dubbing that fits the original timing.

The VideoLingo idea (voice-over in another language, synced to the cut) with
the skill's constraint: **no paid APIs**. Every backend here is either free
or fully offline.

Backends (auto-detected, override with --backend):
  edge-tts   pip install edge-tts     free Microsoft neural voices, no key,
                                      dozens of languages, very natural
  piper      `piper` on PATH          fully offline neural TTS
  espeak     `espeak-ng` on PATH      offline, robotic - use as a fallback

How the sync works (the part that makes it usable):
  1. each caption line is rendered separately;
  2. if the rendered speech is longer than its slot, it is time-compressed
     with rubberband/atempo (pitch preserved) up to --max-speed;
  3. if it is still too long, the following gap is borrowed;
  4. lines are laid out on a silent timeline at their exact start times.

Usage:
  python3 dub.py voices --lang en                 # list free voices
  python3 dub.py speak --srt subs_en.srt --lang en --out voice.wav
  python3 dub.py dub --video cut.mp4 --srt subs_en.srt --lang en \\
      --keep-original -18 --out cut_en.mp4
  python3 dub.py backends

Typical pipeline for a second-language version of an ad:
  transcribe.py → localize.py translate → dub.py dub → audio_pro.py normalize
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops        # noqa: E402
import captions   # noqa: E402

SR = 48000

DEFAULT_VOICE = {
    "en": "en-US-AriaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "pt": "pt-BR-FranciscaNeural",
    "it": "it-IT-ElsaNeural",
    "tr": "tr-TR-EmelNeural",
    "hi": "hi-IN-SwaraNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ar": "ar-EG-SalmaNeural",
}


def _have_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def available_backends():
    found = []
    if shutil.which("edge-tts") or _have_module("edge_tts"):
        found.append("edge-tts")
    if shutil.which("piper"):
        found.append("piper")
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        found.append("espeak")
    return found


def pick_backend(backend=None):
    found = available_backends()
    backend = backend or (found[0] if found else None)
    if not backend:
        raise ops.InputError(
            "no free TTS backend found. Install one:\n"
            "  pip install edge-tts        (free neural voices, recommended)\n"
            "  piper                       (fully offline neural TTS)\n"
            "  apt install espeak-ng       (offline fallback)")
    return backend


def list_voices(lang=None):
    """Free voice catalogue from edge-tts, filtered by language prefix."""
    exe = shutil.which("edge-tts")
    if not exe:
        return {"note": "install edge-tts to list neural voices",
                "defaults": DEFAULT_VOICE}
    r = subprocess.run([exe, "--list-voices"], capture_output=True, text=True)
    names = []
    for line in r.stdout.splitlines():
        tok = line.strip().split()
        if tok and "-" in tok[0] and "Neural" in tok[0]:
            if not lang or tok[0].lower().startswith(lang.lower()):
                names.append(tok[0])
    return {"voices": names[:200], "count": len(names)}


# --------------------------------------------------------------------------
# rendering one line
# --------------------------------------------------------------------------
def _tts_edge(text, dst, voice, rate="+0%"):
    exe = shutil.which("edge-tts")
    mp3 = dst + ".mp3"
    cmd = ([exe] if exe else [sys.executable, "-m", "edge_tts"]) + \
        ["--voice", voice, "--rate", rate, "--text", text, "--write-media", mp3]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(mp3):
        raise RuntimeError("edge-tts failed: " + (r.stderr or "")[-500:])
    ops.run(["-i", mp3, "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst])
    os.unlink(mp3)
    return dst


def _tts_piper(text, dst, voice, rate="+0%"):
    exe = shutil.which("piper")
    cmd = [exe, "--output_file", dst]
    if voice and os.path.exists(voice):
        cmd = [exe, "--model", voice, "--output_file", dst]
    r = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("piper failed: " + (r.stderr or "")[-500:])
    return dst


def _tts_espeak(text, dst, voice, rate="+0%"):
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    cmd = [exe, "-w", dst]
    if voice:
        cmd += ["-v", voice]
    cmd += [text]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("espeak failed: " + (r.stderr or "")[-500:])
    return dst


_TTS = {"edge-tts": _tts_edge, "piper": _tts_piper, "espeak": _tts_espeak}


def _fit(src, dst, target, max_speed=1.35):
    """Time-compress a rendered line into its slot, keeping the pitch."""
    dur = ops._dur(src)
    if dur <= 0 or target <= 0:
        shutil.copy(src, dst)
        return 1.0
    speed = dur / target
    speed = max(1.0, min(speed, max_speed))
    if speed <= 1.001:
        shutil.copy(src, dst)
        return 1.0
    chain = ops._atempo_chain(speed) if hasattr(ops, "_atempo_chain") else \
        f"atempo={speed:.4f}"
    ops.run(["-i", src, "-af", chain, "-ac", "1", "-ar", str(SR),
             "-c:a", "pcm_s16le", dst])
    return round(speed, 3)


def _segments_from(srt=None, words=None, per_line=3, max_chars=42):
    if srt:
        items = captions.parse_srt(srt)
    elif words:
        with open(words, encoding="utf-8") as fh:
            items = json.load(fh)
    else:
        raise ops.InputError("pass --srt subs.srt or --words words.json")
    # merge word-level entries into speakable lines
    groups = captions.group_words(items, per_line=per_line, max_chars=max_chars)
    return [{"text": " ".join(str(x["text"]) for x in g),
             "start": float(g[0]["start"]), "end": float(g[-1]["end"])}
            for g in groups]


def speak(segments, out_wav, lang="en", voice=None, backend=None,
          max_speed=1.35, total=None):
    """Render every segment and lay them out on one silent timeline."""
    backend = pick_backend(backend)
    voice = voice or DEFAULT_VOICE.get(lang, DEFAULT_VOICE["en"])
    if backend == "espeak" and voice and "Neural" in voice:
        voice = lang
    tmp = tempfile.mkdtemp(prefix="dub_")

    rendered = []
    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        raw = os.path.join(tmp, f"raw{i}.wav")
        fit = os.path.join(tmp, f"fit{i}.wav")
        _TTS[backend](text, raw, voice)
        slot = max(0.2, float(seg["end"]) - float(seg["start"]))
        nxt = segments[i + 1]["start"] if i + 1 < len(segments) else None
        if nxt is not None:                       # borrow the following gap
            slot = max(slot, float(nxt) - float(seg["start"]) - 0.05)
        speed = _fit(raw, fit, slot, max_speed)
        rendered.append({"path": fit, "start": float(seg["start"]),
                         "speed": speed, "dur": round(ops._dur(fit), 3),
                         "text": text})

    if not rendered:
        raise ops.InputError("nothing to speak - the caption source was empty")

    length = total or max(r["start"] + r["dur"] for r in rendered) + 0.3
    args = ["-f", "lavfi", "-t", f"{length:.3f}",
            "-i", f"anullsrc=r={SR}:cl=mono"]
    for r in rendered:
        args += ["-i", r["path"]]
    parts = []
    for i, r in enumerate(rendered, start=1):
        parts.append(f"[{i}:a]adelay={int(r['start'] * 1000)}|"
                     f"{int(r['start'] * 1000)}[d{i}]")
    mix_inputs = "[0:a]" + "".join(f"[d{i}]" for i in range(1, len(rendered) + 1))
    filt = ";".join(parts) + ";" + mix_inputs + \
        f"amix=inputs={len(rendered) + 1}:normalize=0[out]"
    ops.run(args + ["-filter_complex", filt, "-map", "[out]",
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out_wav])
    return {"backend": backend, "voice": voice, "lines": len(rendered),
            "max_speed_used": max(r["speed"] for r in rendered),
            "duration_sec": round(ops._dur(out_wav), 3)}


def dub(video, out, srt=None, words=None, lang="en", voice=None, backend=None,
        keep_original=-99.0, max_speed=1.35, duck=True):
    """Replace the voice track with a dubbed one (optionally keeping ambience)."""
    ops.require_file(video, "video")
    segments = _segments_from(srt, words)
    tmp = tempfile.mkdtemp(prefix="dubmix_")
    wav = os.path.join(tmp, "voice.wav")
    info = speak(segments, wav, lang, voice, backend, max_speed,
                 total=ops._dur(video))

    if keep_original > -90 and ops._has_audio(video):
        # keep the original bed quietly under the dub, ducked by the new voice
        filt = (f"[0:a]volume={keep_original}dB[bed];"
                "[bed][1:a]sidechaincompress=threshold=0.05:ratio=8:"
                "attack=20:release=300[ducked];"
                "[ducked][1:a]amix=inputs=2:normalize=0[mixed]"
                if duck else
                f"[0:a]volume={keep_original}dB[bed];"
                "[bed][1:a]amix=inputs=2:normalize=0[mixed]")
        ops.run(["-i", video, "-i", wav, "-filter_complex", filt,
                 "-map", "0:v", "-map", "[mixed]", "-c:v", "copy"] +
                ops.AAC + ["-shortest", out])
    else:
        ops.run(["-i", video, "-i", wav, "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy"] + ops.AAC + ["-shortest", out])
    info.update({"output": out, "language": lang,
                 "kept_original_db": keep_original if keep_original > -90 else None})
    return info


def build_parser():
    p = argparse.ArgumentParser(description="Free local TTS dubbing, timing-fit")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backends")
    v = sub.add_parser("voices")
    v.add_argument("--lang", help="filter, e.g. en / ru / es")

    for name in ("speak", "dub"):
        s = sub.add_parser(name)
        s.add_argument("--srt")
        s.add_argument("--words")
        s.add_argument("--lang", default="en")
        s.add_argument("--voice", help="voice name (edge-tts) or model path (piper)")
        s.add_argument("--backend", choices=sorted(_TTS))
        s.add_argument("--max-speed", type=float, default=1.35,
                       help="max time-compression applied to fit a line")
        s.add_argument("--out", required=True)
        if name == "dub":
            s.add_argument("--video", required=True)
            s.add_argument("--keep-original", type=float, default=-99.0,
                           help="gain in dB for the original audio bed "
                                "(e.g. -18); omit to replace it completely")
            s.add_argument("--no-duck", dest="duck", action="store_false")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)

    if a.cmd == "backends":
        print(json.dumps({"ok": True, "available": available_backends(),
                          "default_voices": DEFAULT_VOICE}, indent=2))
        return
    if a.cmd == "voices":
        print(json.dumps({"ok": True, **list_voices(a.lang)},
                         ensure_ascii=False, indent=2))
        return
    if a.cmd == "speak":
        segments = _segments_from(a.srt, a.words)
        info = speak(segments, a.out, a.lang, a.voice, a.backend, a.max_speed)
        print(json.dumps({"ok": True, "output": a.out, **info},
                         ensure_ascii=False, indent=2))
        return

    info = dub(a.video, a.out, a.srt, a.words, a.lang, a.voice, a.backend,
               a.keep_original, a.max_speed, a.duck)
    print(json.dumps({"ok": True, **info}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
