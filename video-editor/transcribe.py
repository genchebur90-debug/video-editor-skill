#!/usr/bin/env python3
"""transcribe.py - OPTIONAL speech-to-text hook for captions.

The skill itself never requires an ASR engine: you can always pass an SRT,
a words JSON, or plain --transcript-text. This helper is a thin, dependency
free wrapper around whatever ASR is already installed on the machine, so that
`captions.py` / `playbook.py` can be fed word-level timings automatically.
Everything here is free and runs locally - no API keys.

Backends, tried in this order (first one found wins, override with --backend):
  whisperx         pip install whisperx            (BEST: forced-alignment,
                                                    real per-word timings,
                                                    low hallucination)
  faster-whisper   pip install faster-whisper      (word timestamps, fast)
  openai-whisper   pip install -U openai-whisper   (word timestamps)
  whisper.cpp      `whisper-cli` / `main` on PATH  (segment timestamps)

Voice separation (optional, big accuracy win on clips with a loud music bed):
  --separate demucs   pip install demucs            (isolates the vocal stem
                                                     before transcribing)
  --separate filter   no install - ffmpeg-only centre/銀-band voice isolation

Usage:
    python3 transcribe.py IN.mp4 --out words.json [--model small] [--lang ru]
    python3 transcribe.py IN.mp4 --out words.json --separate demucs
    python3 transcribe.py --list-backends

Output: JSON list of {"text", "start", "end"} - directly usable as
`captions.py --words words.json` or `playbook.py --captions words.json`.
If only segment-level timings are available they are written as segments;
captions.py expands them into word timings automatically.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402


def _have_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def available_backends():
    found = []
    if _have_module("whisperx"):
        found.append("whisperx")
    if _have_module("faster_whisper"):
        found.append("faster-whisper")
    if _have_module("whisper"):
        found.append("openai-whisper")
    if shutil.which("whisper-cli") or shutil.which("whisper.cpp"):
        found.append("whisper.cpp")
    return found


def available_separators():
    found = ["filter"]  # ffmpeg-only, always available
    if _have_module("demucs") or shutil.which("demucs"):
        found.insert(0, "demucs")
    return found


def extract_wav(src, dst):
    """16 kHz mono WAV - what every whisper build expects."""
    ops.run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst])
    return dst


# --------------------------------------------------------------------------
# voice separation (run BEFORE the ASR pass)
# --------------------------------------------------------------------------
def _separate_filter(wav, dst):
    """ffmpeg-only vocal emphasis: speech band + spectral de-noise + gate.

    Not a true source separation, but it reliably lifts a voice out of a
    music bed enough for whisper to stop guessing lyrics.
    """
    chain = ("highpass=f=120,lowpass=f=7500,"
             "afftdn=nf=-24,"
             "agate=threshold=0.02:ratio=2:attack=10:release=200,"
             "dynaudnorm=f=200:g=5")
    ops.run(["-i", wav, "-af", chain, "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", dst])
    return dst


def _separate_demucs(wav, dst):
    """True source separation via demucs (free, local, CPU-capable)."""
    tmp = tempfile.mkdtemp(prefix="demucs_")
    exe = shutil.which("demucs")
    cmd = ([exe] if exe else [sys.executable, "-m", "demucs"]) + \
        ["--two-stems", "vocals", "-o", tmp, wav]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("demucs failed: " + (r.stderr or "")[-800:])
    vocals = None
    for root, _dirs, files in os.walk(tmp):
        for f in files:
            if f.startswith("vocals."):
                vocals = os.path.join(root, f)
    if not vocals:
        raise RuntimeError("demucs produced no vocals stem")
    ops.run(["-i", vocals, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst])
    return dst


def separate_voice(wav, dst, method="auto"):
    if method in ("auto", None):
        method = available_separators()[0]
    if method == "demucs":
        return _separate_demucs(wav, dst), "demucs"
    if method == "filter":
        return _separate_filter(wav, dst), "filter"
    raise ops.InputError(f"unknown separator: {method}")


# --------------------------------------------------------------------------
# ASR backends
# --------------------------------------------------------------------------
def _via_whisperx(wav, model, lang):
    """WhisperX: transcribe, then force-align for true word boundaries."""
    import whisperx  # noqa: WPS433
    device = "cpu"
    compute_type = "int8"
    try:
        import torch  # noqa: WPS433
        if torch.cuda.is_available():
            device, compute_type = "cuda", "float16"
    except Exception:
        pass

    audio = whisperx.load_audio(wav)
    m = whisperx.load_model(model, device, compute_type=compute_type,
                            language=lang)
    result = m.transcribe(audio, batch_size=8)
    detected = result.get("language") or lang or "en"

    words = []
    try:
        align_model, meta = whisperx.load_align_model(language_code=detected,
                                                      device=device)
        aligned = whisperx.align(result["segments"], align_model, meta, audio,
                                 device, return_char_alignments=False)
        for seg in aligned.get("segments", []):
            for w in seg.get("words", []) or []:
                txt = (w.get("word") or "").strip()
                if not txt:
                    continue
                start = w.get("start")
                end = w.get("end")
                if start is None or end is None:
                    continue
                words.append({"text": txt, "start": round(float(start), 3),
                              "end": round(float(end), 3)})
    except Exception:
        words = []  # alignment model unavailable for this language

    if not words:  # fall back to segment timings
        for seg in result.get("segments", []):
            txt = (seg.get("text") or "").strip()
            if txt:
                words.append({"text": txt,
                              "start": round(float(seg["start"]), 3),
                              "end": round(float(seg["end"]), 3)})
    return words


def _via_faster_whisper(wav, model, lang):
    from faster_whisper import WhisperModel  # noqa: WPS433
    m = WhisperModel(model, device="auto", compute_type="int8")
    segments, _ = m.transcribe(wav, language=lang, word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            txt = (w.word or "").strip()
            if txt:
                words.append({"text": txt, "start": round(w.start, 3),
                              "end": round(w.end, 3)})
        if not seg.words and (seg.text or "").strip():
            words.append({"text": seg.text.strip(), "start": round(seg.start, 3),
                          "end": round(seg.end, 3)})
    return words


def _via_openai_whisper(wav, model, lang):
    import whisper  # noqa: WPS433
    m = whisper.load_model(model)
    res = m.transcribe(wav, language=lang, word_timestamps=True)
    words = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []) or []:
            txt = (w.get("word") or "").strip()
            if txt:
                words.append({"text": txt, "start": round(float(w["start"]), 3),
                              "end": round(float(w["end"]), 3)})
        if not seg.get("words") and (seg.get("text") or "").strip():
            words.append({"text": seg["text"].strip(),
                          "start": round(float(seg["start"]), 3),
                          "end": round(float(seg["end"]), 3)})
    return words


def _via_whisper_cpp(wav, model, lang):
    exe = shutil.which("whisper-cli") or shutil.which("whisper.cpp")
    tmp = tempfile.mkdtemp(prefix="wcpp_")
    base = os.path.join(tmp, "out")
    cmd = [exe, "-f", wav, "-oj", "-of", base]
    if model and os.path.exists(model):
        cmd += ["-m", model]
    if lang:
        cmd += ["-l", lang]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("whisper.cpp failed: " + r.stderr[-800:])
    with open(base + ".json", encoding="utf-8") as fh:
        data = json.load(fh)
    words = []
    for seg in data.get("transcription", []):
        off = seg.get("offsets", {})
        txt = (seg.get("text") or "").strip()
        if txt and "from" in off:
            words.append({"text": txt, "start": round(off["from"] / 1000.0, 3),
                          "end": round(off["to"] / 1000.0, 3)})
    return words


_RUNNERS = {
    "whisperx": _via_whisperx,
    "faster-whisper": _via_faster_whisper,
    "openai-whisper": _via_openai_whisper,
    "whisper.cpp": _via_whisper_cpp,
}


def to_srt(words, path, per_line=7):
    """Group word timings into a plain SRT (handy for review / translation)."""
    def ts(t):
        h = int(t // 3600)
        m = int(t % 3600 // 60)
        s = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    idx = 1
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        text = " ".join(w["text"] for w in chunk).strip()
        lines.append(f"{idx}\n{ts(chunk[0]['start'])} --> {ts(chunk[-1]['end'])}\n{text}\n")
        idx += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def transcribe(src, out=None, model="small", lang=None, backend=None,
               separate=None, srt_out=None):
    ops.require_file(src, "media file")
    found = available_backends()
    backend = backend or (found[0] if found else None)
    if not backend:
        raise ops.InputError(
            "no ASR backend found. Install one of (all free, local):\n"
            "  pip install whisperx            (best word timings)\n"
            "  pip install faster-whisper      (fast, lighter)\n"
            "  pip install -U openai-whisper\n"
            "  or put whisper-cli (whisper.cpp) on PATH\n"
            "Or skip ASR entirely: pass --srt / --words / --transcript-text.")

    tmp = tempfile.mkdtemp(prefix="asr_")
    wav = extract_wav(src, os.path.join(tmp, "audio.wav"))
    used_sep = None
    if separate:
        wav, used_sep = separate_voice(wav, os.path.join(tmp, "voice.wav"),
                                       separate)

    runner = _RUNNERS.get(backend)
    if not runner:
        raise ops.InputError(f"unknown backend: {backend}")
    words = runner(wav, model, lang)

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(words, fh, ensure_ascii=False, indent=2)
    if srt_out:
        to_srt(words, srt_out)
    return words, backend, used_sep


def main(argv=None):
    p = argparse.ArgumentParser(description="Optional ASR -> words JSON for captions")
    p.add_argument("src", nargs="?", help="video or audio file")
    p.add_argument("--out", help="where to write words JSON")
    p.add_argument("--srt", dest="srt_out", help="also write a plain SRT here")
    p.add_argument("--model", default="small",
                   help="whisper model name (tiny/base/small/medium/large-v3), "
                        "or a .bin path for whisper.cpp")
    p.add_argument("--lang", help="language code, e.g. ru / en (default: auto)")
    p.add_argument("--backend", choices=sorted(_RUNNERS))
    p.add_argument("--separate", nargs="?", const="auto",
                   choices=["auto", "demucs", "filter"],
                   help="isolate the voice before transcribing (music beds)")
    p.add_argument("--list-backends", action="store_true")
    a = p.parse_args(argv)

    if a.list_backends:
        print(json.dumps({"ok": True, "available": available_backends(),
                          "separators": available_separators()}, indent=2))
        return
    if not a.src:
        p.error("src is required (or use --list-backends)")

    words, backend, sep = transcribe(a.src, a.out, a.model, a.lang, a.backend,
                                     a.separate, a.srt_out)
    print(json.dumps({"ok": True, "backend": backend, "separation": sep,
                      "words": len(words), "output": a.out or None,
                      "srt": a.srt_out or None,
                      "preview": words[:5]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
