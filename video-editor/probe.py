#!/usr/bin/env python3
"""probe.py - inspect a media file and print clean JSON.

Usage:
    python3 probe.py INPUT

Returns duration, container, video (codec/w/h/fps/pix_fmt/sar/rotation),
audio (codec/sample_rate/channels), and has_video / has_audio flags.
Every other script in this skill relies on this to make decisions.
"""
import json
import os
import subprocess
import sys


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _fps(stream):
    if not stream:
        return None
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key, "0/0")
        try:
            n, d = val.split("/")
            n, d = float(n), float(d)
            if d:
                return round(n / d, 3)
        except Exception:
            pass
    return None


def probe(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", path]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed: " + r.stderr.strip())
    data = json.loads(r.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    out = {
        "path": path,
        "container": fmt.get("format_name"),
        "duration_sec": float(fmt["duration"]) if fmt.get("duration") else None,
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "bitrate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "has_video": v is not None,
        "has_audio": a is not None,
    }
    if v:
        rot = None
        for sd in (v.get("side_data_list") or []):
            if "rotation" in sd:
                rot = sd["rotation"]
        out["video"] = {
            "codec": v.get("codec_name"),
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": _fps(v),
            "pix_fmt": v.get("pix_fmt"),
            "sar": v.get("sample_aspect_ratio"),
            "dar": v.get("display_aspect_ratio"),
            "rotation": rot,
        }
    if a:
        out["audio"] = {
            "codec": a.get("codec_name"),
            "sample_rate": int(a["sample_rate"]) if a.get("sample_rate") else None,
            "channels": a.get("channels"),
            "bitrate": int(a["bit_rate"]) if a.get("bit_rate") else None,
        }
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 probe.py INPUT", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(probe(sys.argv[1]), indent=2))
