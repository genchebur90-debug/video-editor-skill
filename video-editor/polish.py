#!/usr/bin/env python3
"""polish.py - image cleanup: stabilization, denoise, sharpen, grain (block F).

Handheld wobble, sensor noise from phone footage and mushy detail after
scaling are what make an edit look cheap. This module fixes all three, in the
correct order (stabilize -> denoise -> sharpen -> grain), because doing it in
any other order amplifies noise or eats detail.

Ops:
  stabilize IN OUT     two-pass vidstab (detect + transform), auto-cropped
  denoise   IN OUT     hqdn3d (fast) or nlmeans (slow, high quality)
  sharpen   IN OUT     luma-only unsharp, safe amounts
  grain     IN OUT     subtle film grain (hides banding on flat gradients)
  auto      IN OUT     the whole chain, strength-driven
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

DENOISE = {
    "light": "hqdn3d=1.5:1.2:6:6",
    "medium": "hqdn3d=3:2:8:8",
    "heavy": "nlmeans=s=4.0:p=7:r=15",
}


def stabilize(src, dst, shakiness=5, smoothing=15, zoom=1.0, optzoom=1):
    """Two-pass vidstab. Pass 1 measures motion, pass 2 warps and crops."""
    ops.require_file(src, "input")
    tmp = tempfile.mkdtemp(prefix="vidstab_")
    trf = os.path.join(tmp, "transforms.trf")
    ops.run(["-i", src, "-vf",
             f"vidstabdetect=shakiness={shakiness}:accuracy=15:result={trf}",
             "-f", "null", "-"])
    vf = (f"vidstabtransform=input={trf}:smoothing={smoothing}:zoom={zoom}:"
          f"optzoom={optzoom}:interpol=bicubic:crop=black,unsharp=5:5:0.3:3:3:0.0")
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    return dst


def denoise(src, dst, level="light"):
    ops.require_file(src, "input")
    vf = DENOISE.get(level, DENOISE["light"])
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    return dst


def sharpen(src, dst, amount=0.6):
    ops.require_file(src, "input")
    amount = max(0.0, min(1.5, float(amount)))
    vf = (f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={amount}:"
          f"chroma_amount=0")
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    return dst


def grain(src, dst, strength=6):
    ops.require_file(src, "input")
    vf = f"noise=alls={int(strength)}:allf=t+u"
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    return dst


def measure_shake(src, sample_sec=6.0):
    """Rough shake estimate from vidstabdetect motion magnitudes."""
    ops.require_file(src, "input")
    tmp = tempfile.mkdtemp(prefix="shake_")
    trf = os.path.join(tmp, "t.trf")
    dur = min(sample_sec, max(0.5, ops._dur(src)))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", f"{dur}", "-i", src,
                    "-vf", f"vidstabdetect=shakiness=8:accuracy=9:result={trf}",
                    "-f", "null", "-"], capture_output=True, text=True)
    if not os.path.exists(trf):
        return 0.0
    mags = []
    with open(trf, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            for mx, my in re.findall(r"\(([-\d.]+)\s+([-\d.]+)\)", line)[:1]:
                mags.append(abs(float(mx)) + abs(float(my)))
    if not mags:
        return 0.0
    return round(sum(mags) / len(mags), 3)


def auto(src, dst, strength="auto", do_stabilize=None, denoise_level=None,
         sharpen_amount=0.5, grain_strength=0):
    """Analyse the clip and apply only what it actually needs."""
    ops.require_file(src, "input")
    tmp = tempfile.mkdtemp(prefix="polish_")
    ext = os.path.splitext(dst)[1] or ".mp4"
    cur = src
    applied = []

    shake = measure_shake(src) if do_stabilize is None else 0.0
    want_stab = do_stabilize if do_stabilize is not None else shake > 0.35
    if want_stab:
        nxt = os.path.join(tmp, "stab" + ext)
        stabilize(cur, nxt)
        cur = nxt
        applied.append(f"stabilize(shake={shake})")

    if denoise_level and denoise_level != "off":
        nxt = os.path.join(tmp, "dn" + ext)
        denoise(cur, nxt, denoise_level)
        cur = nxt
        applied.append(f"denoise({denoise_level})")

    if sharpen_amount and sharpen_amount > 0:
        nxt = os.path.join(tmp, "sh" + ext)
        sharpen(cur, nxt, sharpen_amount)
        cur = nxt
        applied.append(f"sharpen({sharpen_amount})")

    if grain_strength and grain_strength > 0:
        nxt = os.path.join(tmp, "gr" + ext)
        grain(cur, nxt, grain_strength)
        cur = nxt
        applied.append(f"grain({grain_strength})")

    if cur == src:
        ops.run(["-i", src, "-c", "copy", dst])
    else:
        os.replace(cur, dst)
    return dst, {"applied": applied, "shake": shake}


def main(argv=None):
    p = argparse.ArgumentParser(description="Image cleanup and finishing")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stabilize"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--shakiness", type=int, default=5)
    s.add_argument("--smoothing", type=int, default=15)
    s.add_argument("--zoom", type=float, default=1.0)

    s = sub.add_parser("denoise"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--level", default="light", choices=sorted(DENOISE))

    s = sub.add_parser("sharpen"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--amount", type=float, default=0.6)

    s = sub.add_parser("grain"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--strength", type=int, default=6)

    s = sub.add_parser("measure"); s.add_argument("src")

    s = sub.add_parser("auto"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--denoise", dest="denoise_level", default="off",
                   choices=["off"] + sorted(DENOISE))
    s.add_argument("--sharpen", dest="sharpen_amount", type=float, default=0.5)
    s.add_argument("--grain", dest="grain_strength", type=int, default=0)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--stabilize", dest="do_stabilize", action="store_true", default=None)
    g.add_argument("--no-stabilize", dest="do_stabilize", action="store_false")
    a = p.parse_args(argv)

    if a.cmd == "stabilize":
        stabilize(a.src, a.dst, a.shakiness, a.smoothing, a.zoom)
        out = a.dst
    elif a.cmd == "denoise":
        out = denoise(a.src, a.dst, a.level)
    elif a.cmd == "sharpen":
        out = sharpen(a.src, a.dst, a.amount)
    elif a.cmd == "grain":
        out = grain(a.src, a.dst, a.strength)
    elif a.cmd == "measure":
        print(json.dumps({"ok": True, "shake": measure_shake(a.src)}, indent=2))
        return
    else:
        out, info = auto(a.src, a.dst, do_stabilize=a.do_stabilize,
                         denoise_level=a.denoise_level,
                         sharpen_amount=a.sharpen_amount,
                         grain_strength=a.grain_strength)
        print(json.dumps({"ok": True, "output": out, **info}, indent=2))
        return
    print(json.dumps({"ok": True, "output": out,
                      "duration_sec": round(ops._dur(out), 3)}))


if __name__ == "__main__":
    main()
