#!/usr/bin/env python3
"""analyze.py - content-adaptive analysis and edits.

Good editing fits the footage, not a fixed preset. This inspects a clip and
reports the cues a human editor actually uses, then can act on them:

  - silences / speech segments (silencedetect) -> trim dead air, time captions
  - scene changes (scene score)                -> cut candidates
  - loudness (volumedetect)                    -> normalization gain

Ops:
  analyze SRC                 -> JSON of the above
  tighten SRC DST             -> remove dead air (jump-cut pacing) in one pass

The playbook consumes analyze() to decide cut density, punch-in moments and
caption timing per clip, instead of applying a blanket rhythm.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402


def _stderr(args):
    cmd = ["ffmpeg", "-hide_banner", "-nostats"] + args + ["-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def detect_silences(path, noise_db=-30.0, min_d=0.35):
    se = _stderr(["-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_d}"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", se)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", se)]
    dur = ops._dur(path)
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else dur
        out.append([round(max(0.0, s), 3), round(min(dur, e), 3)])
    return out


def speech_segments(path, noise_db=-30.0, min_d=0.35, pad=0.08, min_seg=0.20):
    dur = ops._dur(path)
    sil = detect_silences(path, noise_db, min_d)
    segs, cur = [], 0.0
    for s, e in sil:
        if s > cur:
            segs.append([cur, s])
        cur = max(cur, e)
    if cur < dur:
        segs.append([cur, dur])
    padded = []
    for s, e in segs:
        s2, e2 = max(0.0, s - pad), min(dur, e + pad)
        if e2 - s2 >= min_seg:
            padded.append([round(s2, 3), round(e2, 3)])
    merged = []
    for s, e in padded:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def detect_scenes(path, threshold=0.3):
    se = _stderr(["-i", path, "-vf", f"select='gt(scene,{threshold})',showinfo", "-an"])
    ts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", se)]
    return sorted(round(t, 3) for t in ts)


def loudness(path):
    if not ops._has_audio(path):
        return None
    se = _stderr(["-i", path, "-af", "volumedetect", "-vn"])
    mean = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", se)
    mx = re.search(r"max_volume:\s*(-?[0-9.]+) dB", se)
    return {"mean_db": float(mean.group(1)) if mean else None,
            "max_db": float(mx.group(1)) if mx else None}


def analyze(path):
    w, h = ops._dims(path)
    res = {"path": path, "duration_sec": round(ops._dur(path), 3),
           "width": w, "height": h, "has_audio": ops._has_audio(path)}
    if res["has_audio"]:
        res["silences"] = detect_silences(path)
        res["speech_segments"] = speech_segments(path)
        res["loudness"] = loudness(path)
        lm = res["loudness"]["mean_db"] if res["loudness"] else None
        res["suggested_gain_db"] = round(-16.0 - lm, 1) if lm is not None else 0.0
    res["scenes"] = detect_scenes(path)
    return res


def tighten(path, dst, noise_db=-30.0, min_d=0.35, pad=0.08):
    segs = speech_segments(path, noise_db, min_d, pad)
    has_audio = ops._has_audio(path)
    if not segs:
        ops.run(["-i", path, *ops.VENC_V, *(ops.AAC if has_audio else ["-an"]), dst])
        return dst
    vsel = "+".join(f"between(t,{s},{e})" for s, e in segs)
    if has_audio:
        fc = (f"[0:v]select='{vsel}',setpts=N/FRAME_RATE/TB[v];"
              f"[0:a]aselect='{vsel}',asetpts=N/SR/TB[a]")
        ops.run(["-i", path, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                 *ops.VENC_V, *ops.AAC, dst])
    else:
        ops.run(["-i", path, "-vf", f"select='{vsel}',setpts=N/FRAME_RATE/TB",
                 *ops.VENC_V, "-an", dst])
    return dst


def build_parser():
    p = argparse.ArgumentParser(description="Content-adaptive analysis & edits")
    sub = p.add_subparsers(dest="op", required=True)
    s = sub.add_parser("analyze")
    s.add_argument("src")
    s = sub.add_parser("tighten")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--noise-db", type=float, default=-30.0)
    s.add_argument("--min-d", type=float, default=0.35)
    s.add_argument("--pad", type=float, default=0.08)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.op == "analyze":
        print(json.dumps(analyze(args.src), indent=2))
    else:
        out = tighten(args.src, args.dst, args.noise_db, args.min_d, args.pad)
        print(json.dumps({"ok": True, "output": out,
                          "duration_sec": round(ops._dur(out), 3)}))


if __name__ == "__main__":
    main()
