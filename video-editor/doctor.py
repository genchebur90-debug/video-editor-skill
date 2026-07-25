#!/usr/bin/env python3
"""doctor.py - verify this machine can run the video-editor skill.

Usage:
    python3 doctor.py            # capability checks (exit 0 = ready)
    python3 doctor.py --smoke    # also render a tiny end-to-end test video

Checks ffmpeg/ffprobe on PATH, required encoders (libx264, aac) and filters
(subtitles/ass = libass, xfade, zoompan, sidechaincompress, ...), Python
version, and prints per-OS install hints for anything missing.
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile

REQUIRED_ENCODERS = ["libx264", "aac"]
REQUIRED_FILTERS = ["subtitles", "ass", "xfade", "zoompan", "sidechaincompress",
                    "silencedetect", "volumedetect", "amix", "atempo",
                    "overlay", "concat", "acrossfade"]

HINTS = {
    "Darwin": ["brew install ffmpeg"],
    "Linux": ["sudo apt install ffmpeg          # Debian/Ubuntu",
              "sudo dnf install ffmpeg          # Fedora/RHEL",
              "static build: https://johnvansickle.com/ffmpeg/"],
    "Windows": ["winget install Gyan.FFmpeg", "choco install ffmpeg"],
}


def _out(args):
    try:
        return subprocess.run(args, capture_output=True, text=True).stdout
    except Exception:
        return ""


def main():
    argv = sys.argv[1:]
    ok = True
    rows = []

    for tool in ("ffmpeg", "ffprobe"):
        p = shutil.which(tool)
        rows.append(("PASS" if p else "FAIL", tool, p or "not on PATH"))
        ok = ok and bool(p)

    good_py = sys.version_info >= (3, 8)
    rows.append(("PASS" if good_py else "FAIL", "python", platform.python_version()))
    ok = ok and good_py

    if shutil.which("ffmpeg"):
        head = _out(["ffmpeg", "-version"]).splitlines()
        rows.append(("INFO", "version", head[0][:72] if head else "?"))
        enc = _out(["ffmpeg", "-hide_banner", "-encoders"])
        for e in REQUIRED_ENCODERS:
            hit = f" {e} " in enc
            rows.append(("PASS" if hit else "FAIL", f"encoder {e}",
                         "found" if hit else "MISSING"))
            ok = ok and hit
        flt = _out(["ffmpeg", "-hide_banner", "-filters"])
        for f in REQUIRED_FILTERS:
            hit = f" {f} " in flt
            rows.append(("PASS" if hit else "FAIL", f"filter {f}",
                         "found" if hit else "MISSING"))
            ok = ok and hit
        rows.append(("INFO", "filter drawtext",
                     "present" if " drawtext " in flt else
                     "absent (fine - this skill renders text via libass)"))

    for st, name, detail in rows:
        print(f"{st:4} | {name:22} | {detail}")

    if not ok:
        print("\nInstall hints for", platform.system())
        for h in HINTS.get(platform.system(), HINTS["Linux"]):
            print("  " + h)
        sys.exit(1)

    if "--smoke" in argv:
        print("\nsmoke: rendering a tiny test video ...")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ops
        import captions
        tmp = tempfile.mkdtemp(prefix="doctor_")
        try:
            src = os.path.join(tmp, "src.mp4")
            ops.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                     "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                     "-t", "1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", src])
            v = os.path.join(tmp, "v.mp4")
            ops.reframe(src, v, 360, 640, mode="fill")
            words = captions.words_from_text("smoke test passed", 1.4)
            ass = captions.build_ass(words, w=360, h=640, style="tiktok")
            out = os.path.join(tmp, "out.mp4")
            ops.burn_subtitles(v, ass, out)
            print(f"smoke: OK ({ops._dur(out):.2f}s vertical clip with captions)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\nREADY - environment can run the video-editor skill.")


if __name__ == "__main__":
    main()
