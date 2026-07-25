#!/usr/bin/env python3
"""fetch.py - pull source footage / reference videos from a URL (yt-dlp).

Free and local: uses yt-dlp if it is installed. Nothing is uploaded anywhere.

  python3 fetch.py URL --out clip.mp4 [--audio-only] [--max-height 1080]
  python3 fetch.py --check

Only download material you have the right to use.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402


def _exe():
    return shutil.which("yt-dlp") or shutil.which("youtube-dl")


def available():
    return bool(_exe())


def fetch(url, out, audio_only=False, max_height=1080):
    exe = _exe()
    if not exe:
        raise ops.InputError(
            "yt-dlp is not installed (free): pip install -U yt-dlp")
    if audio_only:
        fmt = "bestaudio/best"
    else:
        fmt = (f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
               f"best[height<={max_height}]/best")
    cmd = [exe, "-f", fmt, "--no-playlist", "-o", out, url]
    if not audio_only:
        cmd += ["--merge-output-format", "mp4"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("yt-dlp failed: " + (r.stderr or "")[-800:])
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Download source footage via yt-dlp")
    p.add_argument("url", nargs="?")
    p.add_argument("--out", default="source.mp4")
    p.add_argument("--audio-only", action="store_true")
    p.add_argument("--max-height", type=int, default=1080)
    p.add_argument("--check", action="store_true")
    a = p.parse_args(argv)

    if a.check:
        print(json.dumps({"ok": True, "yt_dlp": available()}, indent=2))
        return
    if not a.url:
        p.error("url is required (or use --check)")

    out = fetch(a.url, a.out, a.audio_only, a.max_height)
    print(json.dumps({"ok": True, "output": out,
                      "duration_sec": round(ops._dur(out), 3)
                      if not a.audio_only else None}, indent=2))


if __name__ == "__main__":
    main()
