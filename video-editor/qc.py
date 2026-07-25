#!/usr/bin/env python3
"""qc.py - automated quality control before you publish (block G, QC half).

Professional delivery always ends with a QC pass. This one checks the things
that actually get videos rejected, throttled, or turned down by the platform:

  loudness   integrated LUFS / LRA / true peak vs the platform target
  clipping   sample peaks and clipped-sample count (astats)
  silence    dead audio at the head, tail, or middle
  black      unintended black frames (blackdetect)
  freeze     frozen picture, usually a botched concat (freezedetect)
  format     resolution, aspect ratio, fps, GOP-friendly duration, faststart,
             colour tagging, audio sample rate, duration vs platform limits

Exit code is 0 when there are no FAILs, 1 otherwise, so it can gate a script.

Usage:
  python3 qc.py OUT.mp4 --platform reels
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

# platform -> target LUFS, true peak ceiling, max duration, expected AR
TARGETS = {
    "reels": (-14.0, -1.0, 90, 9 / 16),
    "tiktok": (-14.0, -1.0, 600, 9 / 16),
    "shorts": (-14.0, -1.0, 180, 9 / 16),
    "youtube": (-14.0, -1.0, None, 16 / 9),
    "none": (None, None, None, None),
}


def _ff(args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args],
                          capture_output=True, text=True).stderr


def loudness(path):
    err = _ff(["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err[-3000:]
    def grab(label):
        m = re.search(label + r":\s*(-?\d+\.?\d*)", tail)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab("I"), "lra": grab("LRA"),
            "true_peak_dbtp": grab("Peak")}


def audio_stats(path):
    err = _ff(["-i", path, "-af", "astats=metadata=1:reset=0", "-f", "null", "-"])
    def grab(label):
        vals = [float(v) for v in re.findall(label + r":\s*(-?\d+\.?\d*)", err)]
        return max(vals) if vals else None
    return {"peak_dbfs": grab("Peak level dB"),
            "flat_factor": grab("Flat factor"),
            "clipped_samples": grab("Number of clipped samples")}


def black_frames(path):
    err = _ff(["-i", path, "-vf", "blackdetect=d=0.4:pic_th=0.98", "-f", "null", "-"])
    return [{"start": float(a), "duration": float(b)} for a, b in
            re.findall(r"black_start:(\d+\.?\d*).*?black_duration:(\d+\.?\d*)", err)]


def freezes(path):
    err = _ff(["-i", path, "-vf", "freezedetect=n=-60dB:d=1.0", "-f", "null", "-"])
    return [float(v) for v in re.findall(r"freeze_start:\s*(\d+\.?\d*)", err)]


def silences(path):
    if not ops._has_audio(path):
        return []
    err = _ff(["-i", path, "-af", "silencedetect=n=-50dB:d=1.0", "-f", "null", "-"])
    return [float(v) for v in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", err)]


def container(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json",
         path], capture_output=True, text=True).stdout
    data = json.loads(out or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fr = v.get("avg_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        fps = round(float(num) / float(den), 3) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "width": v.get("width"), "height": v.get("height"), "fps": fps,
        "vcodec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "color_trc": v.get("color_transfer"), "color_space": v.get("color_space"),
        "acodec": a.get("codec_name"),
        "sample_rate": int(a.get("sample_rate", 0) or 0),
        "channels": a.get("channels"),
        "duration": round(float(data.get("format", {}).get("duration", 0) or 0), 3),
        "bitrate_kbps": round(float(data.get("format", {}).get("bit_rate", 0) or 0) / 1000),
        "size_bytes": int(data.get("format", {}).get("size", 0) or 0),
    }


def faststart(path):
    """True when moov sits before mdat (instant playback while downloading)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(2 * 1024 * 1024)
    except OSError:
        return None
    m, d = head.find(b"moov"), head.find(b"mdat")
    if m == -1:
        return False
    return d == -1 or m < d


def report(path, platform="reels"):
    ops.require_file(path, "input")
    target, tp_max, max_sec, ar = TARGETS.get(platform, TARGETS["reels"])
    c = container(path)
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    has_audio = ops._has_audio(path)
    loud = loudness(path) if has_audio else {}
    ast = audio_stats(path) if has_audio else {}

    if has_audio and target is not None and loud.get("integrated_lufs") is not None:
        i = loud["integrated_lufs"]
        diff = abs(i - target)
        add("loudness", "PASS" if diff <= 1.0 else "WARN" if diff <= 2.5 else "FAIL",
            f"{i} LUFS (target {target}, delta {round(i - target, 2)})")
    elif not has_audio:
        add("loudness", "FAIL", "no audio stream")

    if has_audio and loud.get("true_peak_dbtp") is not None and tp_max is not None:
        tp = loud["true_peak_dbtp"]
        add("true_peak", "PASS" if tp <= tp_max else "WARN" if tp <= 0 else "FAIL",
            f"{tp} dBTP (ceiling {tp_max})")

    if has_audio:
        clipped = ast.get("clipped_samples") or 0
        add("clipping", "PASS" if clipped == 0 else "WARN" if clipped < 50 else "FAIL",
            f"{int(clipped)} clipped samples")
        add("sample_rate", "PASS" if c["sample_rate"] == 48000 else "WARN",
            f"{c['sample_rate']} Hz (48000 recommended)")
        sil = silences(path)
        add("silence", "PASS" if not sil else "WARN",
            "none" if not sil else f"{len(sil)} silent stretch(es) at {sil[:5]}")

    blacks = black_frames(path)
    add("black_frames", "PASS" if not blacks else "WARN",
        "none" if not blacks else f"{len(blacks)} at {[b['start'] for b in blacks[:5]]}")

    fr = freezes(path)
    add("freeze", "PASS" if not fr else "WARN",
        "none" if not fr else f"frozen picture at {fr[:5]}")

    if c["width"] and c["height"]:
        got_ar = c["width"] / float(c["height"])
        ok_ar = ar is None or abs(got_ar - ar) < 0.02
        add("aspect_ratio", "PASS" if ok_ar else "WARN",
            f"{c['width']}x{c['height']} ({round(got_ar, 3)})")
        add("resolution", "PASS" if min(c["width"], c["height"]) >= 720 else "WARN",
            f"{c['width']}x{c['height']}")
    add("fps", "PASS" if c["fps"] >= 24 else "WARN", f"{c['fps']} fps")
    add("pixel_format", "PASS" if c["pix_fmt"] == "yuv420p" else "WARN",
        str(c["pix_fmt"]))
    add("color_tagging", "PASS" if c["color_space"] in ("bt709", "smpte170m") else "WARN",
        f"space={c['color_space']} trc={c['color_trc']} (bt709 expected)")
    fs = faststart(path)
    add("faststart", "PASS" if fs else "WARN",
        "moov before mdat" if fs else "remux with -movflags +faststart")
    if max_sec and c["duration"] > max_sec:
        add("duration", "FAIL", f"{c['duration']}s exceeds {platform} limit {max_sec}s")
    else:
        add("duration", "PASS", f"{c['duration']}s")

    fails = sum(1 for x in checks if x["status"] == "FAIL")
    warns = sum(1 for x in checks if x["status"] == "WARN")
    return {"file": path, "platform": platform, "container": c,
            "loudness": loud, "audio_stats": ast,
            "checks": checks, "fails": fails, "warnings": warns,
            "verdict": "FAIL" if fails else "WARN" if warns else "PASS"}


def main(argv=None):
    p = argparse.ArgumentParser(description="Pre-publish quality control")
    p.add_argument("src")
    p.add_argument("--platform", default="reels", choices=sorted(TARGETS))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    a = p.parse_args(argv)

    rep = report(a.src, a.platform)
    if a.json:
        print(json.dumps({"ok": rep["verdict"] != "FAIL", **rep}, indent=2))
    else:
        print(f"QC {os.path.basename(a.src)} [{a.platform}] -> {rep['verdict']}")
        for c in rep["checks"]:
            print(f"  {c['status']:4}  {c['check']:<14} {c['detail']}")
    bad = rep["fails"] or (a.strict and rep["warnings"])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
