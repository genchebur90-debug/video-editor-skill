#!/usr/bin/env python3
"""color.py - colour management and grading (block C).

Ungraded footage is the second-biggest amateur tell after bad audio. This
module adds the colour stage the old pipeline was missing entirely:

  - HDR (HLG / PQ) -> SDR tone mapping, so iPhone / modern-camera footage does
    not come out washed out or clipped
  - measurement-driven auto grade: black/white point stretch, white balance,
    contrast and saturation derived from signalstats, not guessed
  - film-style look presets and .cube LUT support
  - correct BT.709 tagging on export so players and platforms interpret the
    colours the way you graded them

Ops:
  stats IN                -> measured colour statistics JSON
  grade IN OUT [...]      -> apply tone map + auto grade + look/LUT
  looks                   -> list the built-in looks
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

BT709_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709",
              "-color_trc", "bt709", "-color_range", "tv"]

# Looks are deliberately restrained: strong enough to read as "graded",
# subtle enough to survive platform re-encoding.
LOOKS = {
    "none": "",
    "clean": "eq=contrast=1.04:saturation=1.05:gamma=1.01",
    "punch": ("curves=master='0/0 0.25/0.20 0.75/0.80 1/1',"
              "eq=contrast=1.12:saturation=1.18:gamma=0.99"),
    "teal_orange": ("colorbalance=rs=0.04:gs=-0.01:bs=-0.06:"
                    "rm=0.02:gm=0.0:bm=-0.02:rh=-0.05:gh=0.0:bh=0.06,"
                    "eq=contrast=1.10:saturation=1.12"),
    "film": ("curves=master='0/0.03 0.25/0.24 0.75/0.78 1/0.97',"
             "eq=contrast=1.05:saturation=0.95,"
             "colorbalance=rs=0.02:bs=0.03:rh=0.02:bh=-0.02"),
    "warm_ad": ("colortemperature=temperature=5200,"
                "eq=contrast=1.08:saturation=1.10:brightness=0.01"),
    "cold_tech": ("colortemperature=temperature=7800,"
                  "eq=contrast=1.10:saturation=1.02"),
    "bw": "hue=s=0,curves=master='0/0 0.3/0.26 0.7/0.76 1/1',eq=contrast=1.12",
}


def _probe_color(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries",
         "stream=color_transfer,color_primaries,color_space,pix_fmt",
         "-of", "json", path], capture_output=True, text=True).stdout
    try:
        st = json.loads(out or "{}").get("streams", [{}])[0]
    except (ValueError, IndexError):
        st = {}
    return st


def is_hdr(path):
    st = _probe_color(path)
    trc = (st.get("color_transfer") or "").lower()
    pix = (st.get("pix_fmt") or "").lower()
    return (trc in ("smpte2084", "arib-std-b67")
            or "10le" in pix and trc in ("smpte2084", "arib-std-b67"))


def _esc_movie(path):
    return os.path.abspath(path).replace("\\", "\\\\").replace(":", "\\:") \
        .replace("'", "\\'")


def stats(path, samples=24):
    """Sample signalstats across the clip and average the results."""
    ops.require_file(path, "input")
    dur = max(0.2, ops._dur(path))
    fps = max(0.2, samples / dur)
    keys = ["YAVG", "YMIN", "YMAX", "YLOW", "YHIGH", "SATAVG", "UAVG", "VAVG"]
    entries = ",".join(f"lavfi.signalstats.{k}" for k in keys)
    cmd = ["ffprobe", "-v", "quiet", "-f", "lavfi", "-i",
           f"movie='{_esc_movie(path)}',fps={fps},signalstats",
           "-show_entries", f"frame_tags={entries}", "-of", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    frames = json.loads(out or "{}").get("frames", [])
    acc, n = {k: 0.0 for k in keys}, 0
    for fr in frames:
        tags = fr.get("tags", {})
        try:
            vals = {k: float(tags[f"lavfi.signalstats.{k}"]) for k in keys}
        except (KeyError, ValueError):
            continue
        for k, v in vals.items():
            acc[k] += v
        n += 1
    if not n:
        return {"samples": 0}
    avg = {k: round(acc[k] / n, 2) for k in keys}
    avg["samples"] = n
    avg["is_hdr"] = is_hdr(path)
    # 128 is neutral for U/V; distance from it indicates a colour cast
    avg["cast_u"] = round(avg["UAVG"] - 128.0, 2)
    avg["cast_v"] = round(avg["VAVG"] - 128.0, 2)
    avg["exposure"] = ("dark" if avg["YAVG"] < 90 else
                       "bright" if avg["YAVG"] > 160 else "ok")
    return avg


def auto_chain(st, strength=1.0):
    """Build a corrective filter chain from measured statistics."""
    if not st.get("samples"):
        return ""
    parts = []
    ylow, yhigh = st.get("YLOW", 16.0), st.get("YHIGH", 235.0)
    # only stretch levels when there is real headroom to recover
    if ylow > 24 or yhigh < 225:
        bi = max(0.0, (ylow - 8.0) / 255.0) * strength
        wi = min(1.0, 1.0 - max(0.0, (250.0 - yhigh) / 255.0) * strength)
        parts.append(f"colorlevels=rimin={bi:.4f}:gimin={bi:.4f}:bimin={bi:.4f}:"
                     f"rimax={wi:.4f}:gimax={wi:.4f}:bimax={wi:.4f}")
    cu, cv = st.get("cast_u", 0.0), st.get("cast_v", 0.0)
    if abs(cu) > 3 or abs(cv) > 3:
        # U ~ blue-yellow, V ~ red-cyan; nudge back toward neutral
        rs = round(-cv / 255.0 * 2.0 * strength, 4)
        bs = round(-cu / 255.0 * 2.0 * strength, 4)
        parts.append(f"colorbalance=rm={rs}:bm={bs}")
    yavg = st.get("YAVG", 128.0)
    if yavg < 90:
        parts.append(f"eq=brightness={min(0.12, (100 - yavg) / 900.0):.3f}:gamma=1.06")
    elif yavg > 165:
        parts.append("eq=gamma=0.94")
    sat = st.get("SATAVG", 40.0)
    if sat < 26:
        parts.append("eq=saturation=1.15")
    return ",".join(parts)


def tonemap_chain():
    """HDR (PQ/HLG) -> SDR BT.709, the correct way (linear light + hable)."""
    return ("zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def build_chain(src, look="clean", auto=True, lut=None, strength=1.0,
                contrast=None, saturation=None, temperature=None,
                sharpen=0.0, st=None):
    parts = []
    if is_hdr(src):
        parts.append(tonemap_chain())
    if auto:
        st = st if st is not None else stats(src)
        ch = auto_chain(st, strength)
        if ch:
            parts.append(ch)
    if lut:
        ops.require_file(lut, "LUT file")
        parts.append(f"lut3d=file='{ops._esc_path(os.path.abspath(lut))}'")
    if look and LOOKS.get(look):
        parts.append(LOOKS[look])
    manual = []
    if contrast is not None:
        manual.append(f"contrast={contrast}")
    if saturation is not None:
        manual.append(f"saturation={saturation}")
    if manual:
        parts.append("eq=" + ":".join(manual))
    if temperature:
        parts.append(f"colortemperature=temperature={temperature}")
    if sharpen and sharpen > 0:
        # gentle, luma-only sharpening - never sharpen chroma
        parts.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharpen}"
                     f":chroma_amount=0")
    parts.append("format=yuv420p")
    return ",".join(p for p in parts if p)


def grade(src, dst, look="clean", auto=True, lut=None, strength=1.0,
          contrast=None, saturation=None, temperature=None, sharpen=0.0):
    ops.require_file(src, "input")
    st = stats(src) if auto else {}
    vf = build_chain(src, look, auto, lut, strength, contrast, saturation,
                     temperature, sharpen, st=st)
    aud = ["-c:a", "copy"] if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *BT709_TAGS, *aud, dst])
    return dst, {"chain": vf, "stats": st}


def main(argv=None):
    p = argparse.ArgumentParser(description="Colour management and grading")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats"); s.add_argument("src")
    s.add_argument("--samples", type=int, default=24)

    s = sub.add_parser("grade"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--look", default="clean", choices=sorted(LOOKS))
    s.add_argument("--lut", help="path to a .cube LUT")
    s.add_argument("--no-auto", dest="auto", action="store_false")
    s.add_argument("--strength", type=float, default=1.0)
    s.add_argument("--contrast", type=float)
    s.add_argument("--saturation", type=float)
    s.add_argument("--temperature", type=int)
    s.add_argument("--sharpen", type=float, default=0.0)

    sub.add_parser("looks")
    a = p.parse_args(argv)

    if a.cmd == "looks":
        print(json.dumps({"ok": True, "looks": sorted(LOOKS)}, indent=2))
    elif a.cmd == "stats":
        print(json.dumps({"ok": True, **stats(a.src, a.samples)}, indent=2))
    else:
        _, info = grade(a.src, a.dst, a.look, a.auto, a.lut, a.strength,
                        a.contrast, a.saturation, a.temperature, a.sharpen)
        print(json.dumps({"ok": True, "output": a.dst, "look": a.look,
                          "chain": info["chain"]}, indent=2))


if __name__ == "__main__":
    main()
