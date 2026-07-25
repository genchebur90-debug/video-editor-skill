#!/usr/bin/env python3
"""reframe_smart.py - subject-aware 16:9 -> 9:16 reframing (block B).

A centre crop is the single most obvious "amateur" tell when a landscape clip
is forced into a vertical frame: heads get sliced, the speaker drifts out of
shot. This module tracks where the interesting part of the frame actually is
and pans a virtual camera to follow it.

How it works (pure stdlib - no OpenCV required):
  1. ffmpeg decodes a tiny greyscale proxy (default 64x36 @ 4 fps) to stdout.
  2. Per sample we score each column by temporal motion + spatial contrast and
     take the weighted centroid -> "where the action is".
  3. The centroid track is smoothed (moving average + slew limiting) so the
     camera glides like a tripod pan instead of jittering.
  4. The track becomes a piecewise-linear crop expression evaluated per frame.

If OpenCV happens to be installed, `--anchor face` uses face detection for the
anchor and falls back to motion automatically when no face is found.

Usage:
  python3 reframe_smart.py IN OUT --width 1080 --height 1920
  python3 reframe_smart.py IN --analyze-only            # inspect the track
  python3 reframe_smart.py IN OUT --anchor face --safe tiktok
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

PROXY_W, PROXY_H = 64, 36

# Fraction of the frame covered by platform UI. Subjects are biased away from
# these zones so captions/buttons never sit on someone's face.
SAFE_ZONES = {
    "tiktok": {"bottom": 0.20, "right": 0.14, "top": 0.08},
    "reels": {"bottom": 0.22, "right": 0.12, "top": 0.10},
    "shorts": {"bottom": 0.16, "right": 0.12, "top": 0.08},
    "none": {"bottom": 0.0, "right": 0.0, "top": 0.0},
}


def sample_luma(path, sample_fps=4.0, pw=PROXY_W, ph=PROXY_H):
    """Decode a greyscale proxy and return a list of frames (list of ints)."""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"fps={sample_fps},scale={pw}:{ph}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("proxy decode failed: " + r.stderr.decode()[-800:])
    buf = r.stdout
    n = pw * ph
    return [buf[i:i + n] for i in range(0, len(buf) - n + 1, n)]


def column_scores(frames, pw=PROXY_W, ph=PROXY_H):
    """Per frame, a list of per-column interest scores."""
    out = []
    prev = None
    for fr in frames:
        cols = [0.0] * pw
        for y in range(ph):
            row = y * pw
            for x in range(pw):
                v = fr[row + x]
                # spatial contrast against the horizontal neighbour
                if x + 1 < pw:
                    cols[x] += abs(v - fr[row + x + 1]) * 0.35
                # temporal motion vs the previous sample (weighted higher)
                if prev is not None:
                    cols[x] += abs(v - prev[row + x]) * 1.0
        out.append(cols)
        prev = fr
    return out


def _centroid(cols, pw=PROXY_W, floor_ratio=0.35):
    """Weighted centre of the interest distribution, in 0..1."""
    peak = max(cols) if cols else 0.0
    if peak <= 0:
        return 0.5, 0.0
    floor = peak * floor_ratio
    num = den = 0.0
    for i, c in enumerate(cols):
        w = max(0.0, c - floor)
        num += w * (i + 0.5)
        den += w
    if den <= 0:
        return 0.5, 0.0
    return (num / den) / pw, peak


def _faces_track(path, sample_fps):
    """Optional OpenCV face track. Returns None when unavailable."""
    try:
        import cv2  # noqa: WPS433
    except Exception:
        return None
    cascade_path = os.path.join(getattr(cv2, "data", None).haarcascades,
                               "haarcascade_frontalface_default.xml") \
        if hasattr(cv2, "data") else None
    if not cascade_path or not os.path.exists(cascade_path):
        return None
    cascade = cv2.CascadeClassifier(cascade_path)
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / sample_fps)))
    track, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            small = cv2.resize(frame, (320, 180))
            grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(grey, 1.2, 4, minSize=(24, 24))
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                track.append((x + fw / 2.0) / 320.0)
            else:
                track.append(None)
        idx += 1
    cap.release()
    return track or None


def smooth_track(raw, sample_fps, smooth_sec=1.5, max_pan_per_sec=0.10):
    """Moving average + slew limiting -> a track a human operator could pan."""
    if not raw:
        return [0.5]
    filled, last = [], 0.5
    for v in raw:
        last = last if v is None else v
        filled.append(last)
    win = max(1, int(round(smooth_sec * sample_fps)))
    avg = []
    for i in range(len(filled)):
        lo, hi = max(0, i - win // 2), min(len(filled), i + win // 2 + 1)
        chunk = filled[lo:hi]
        avg.append(sum(chunk) / len(chunk))
    max_step = max_pan_per_sec / max(0.1, sample_fps)
    out = [avg[0]]
    for v in avg[1:]:
        prev = out[-1]
        out.append(prev + max(-max_step, min(max_step, v - prev)))
    return out


def _keyframes(track, sample_fps, max_points=48):
    """Downsample the track to a manageable set of (t, x) keyframes."""
    n = len(track)
    if n <= max_points:
        return [(i / sample_fps, track[i]) for i in range(n)]
    step = n / float(max_points)
    pts = []
    for k in range(max_points):
        i = min(n - 1, int(round(k * step)))
        pts.append((i / sample_fps, track[i]))
    return pts


def crop_x_expr(keyframes, iw, crop_w):
    """Piecewise-linear ffmpeg expression for the crop x position."""
    max_x = max(0, iw - crop_w)

    def px(cx):
        return max(0.0, min(float(max_x), cx * iw - crop_w / 2.0))

    if len(keyframes) == 1:
        return f"{px(keyframes[0][1]):.1f}"
    expr = f"{px(keyframes[-1][1]):.1f}"
    for i in range(len(keyframes) - 2, -1, -1):
        t0, c0 = keyframes[i]
        t1, c1 = keyframes[i + 1]
        x0, x1 = px(c0), px(c1)
        span = max(1e-3, t1 - t0)
        seg = f"({x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{span:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def analyze(src, sample_fps=4.0, anchor="motion", smooth=1.5, max_pan=0.10,
            bias=0.5, bias_strength=0.25):
    ops.require_file(src, "input")
    raw = None
    used = anchor
    if anchor == "face":
        raw = _faces_track(src, sample_fps)
        used = "face" if raw and any(v is not None for v in raw) else "motion"
    if raw is None or used == "motion":
        if anchor == "center":
            frames = []
            used = "center"
        else:
            frames = sample_luma(src, sample_fps)
            used = "motion"
        if used == "center":
            raw = [0.5]
        else:
            raw = [_centroid(cols)[0] for cols in column_scores(frames)]
    # pull the framing slightly toward the composition bias (rule of thirds)
    raw = [None if v is None else v * (1 - bias_strength) + bias * bias_strength
           for v in raw]
    track = smooth_track(raw, sample_fps, smooth, max_pan)
    return {"anchor": used, "sample_fps": sample_fps,
            "samples": len(track),
            "track": [round(v, 4) for v in track],
            "drift": round(max(track) - min(track), 4) if track else 0.0}


def reframe(src, dst, width=1080, height=1920, anchor="motion", sample_fps=4.0,
            smooth=1.5, max_pan=0.10, safe="reels", fps=None, zoom=1.0):
    """Subject-aware crop + scale to the target aspect ratio."""
    ops.require_file(src, "input")
    iw, ih = ops._dims(src)
    if not iw or not ih:
        raise ops.InputError(f"no video stream in {src}")

    zones = SAFE_ZONES.get(safe, SAFE_ZONES["reels"])
    # keep the subject away from the UI on the right-hand side
    bias = 0.5 - zones["right"] * 0.5
    info = analyze(src, sample_fps, anchor, smooth, max_pan, bias=bias)

    target_ar = width / float(height)
    crop_w = min(iw, int(round(ih * target_ar / max(1.0, zoom))))
    crop_h = min(ih, int(round(crop_w / target_ar)))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2

    # vertical placement: lift the frame so faces sit on the upper third and
    # the platform's bottom UI covers empty floor, not the subject
    head_room = zones["bottom"] * 0.5
    y = int(round(max(0, min(ih - crop_h, (ih - crop_h) * (0.5 - head_room)))))

    xexpr = crop_x_expr(_keyframes(info["track"], sample_fps), iw, crop_w)
    # crop's x/y expressions are re-evaluated every frame in ffmpeg (the
    # options are timeline-enabled), so the pan animates without eval=frame,
    # which some static builds do not expose.
    vf = (f"crop={crop_w}:{crop_h}:x='{xexpr}':y={y},"
          f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p")
    if fps:
        vf += f",fps={fps}"
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    info.update({"crop": [crop_w, crop_h], "source": [iw, ih],
                 "output": [width, height], "safe": safe})
    return dst, info


def main(argv=None):
    p = argparse.ArgumentParser(description="Subject-aware vertical reframing")
    p.add_argument("src")
    p.add_argument("dst", nargs="?")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--anchor", default="motion", choices=["motion", "face", "center"])
    p.add_argument("--sample-fps", type=float, default=4.0)
    p.add_argument("--smooth", type=float, default=1.5,
                   help="seconds of moving-average smoothing on the pan")
    p.add_argument("--max-pan", type=float, default=0.10,
                   help="max pan speed as a fraction of frame width per second")
    p.add_argument("--zoom", type=float, default=1.0,
                   help=">1 crops tighter on the subject")
    p.add_argument("--safe", default="reels", choices=sorted(SAFE_ZONES))
    p.add_argument("--fps", type=int)
    p.add_argument("--analyze-only", action="store_true")
    a = p.parse_args(argv)

    if a.analyze_only or not a.dst:
        info = analyze(a.src, a.sample_fps, a.anchor, a.smooth, a.max_pan)
        print(json.dumps({"ok": True, **info}, indent=2))
        return
    _, info = reframe(a.src, a.dst, a.width, a.height, a.anchor, a.sample_fps,
                      a.smooth, a.max_pan, a.safe, a.fps, a.zoom)
    print(json.dumps({"ok": True, "output": a.dst,
                      "duration_sec": round(ops._dur(a.dst), 3),
                      "anchor": info["anchor"], "crop": info["crop"],
                      "pan_drift": info["drift"]}, indent=2))


if __name__ == "__main__":
    main()
