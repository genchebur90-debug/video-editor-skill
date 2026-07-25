#!/usr/bin/env python3
"""audio_pro.py - broadcast-grade audio chain (block A).

Audio is what separates a professional edit from an amateur one. This module
replaces the old "measure peak, apply static gain" approach with the chain a
real post house uses:

  voice   : highpass -> denoise (afftdn) -> gate -> de-ess -> compressor ->
            presence EQ -> limiter
  loudness: TWO-PASS EBU R128 loudnorm to the platform's integrated target,
            true-peak capped (default -1 dBTP)
  music   : level match + EQ pocket carved around the voice + sidechain
            ducking with musical attack/release + fades

Ops:
  measure IN                      -> loudness JSON (I / LRA / TP / thresholds)
  voice   IN OUT [--preset ...]   -> cleaned dialogue
  normalize IN OUT --platform ..  -> two-pass loudnorm to target
  music   VIDEO MUSIC OUT         -> ducked music bed + master limiting
  master  IN OUT --platform ..    -> voice chain + loudnorm in one go

All platform targets follow published normalization behaviour: social feeds
normalize to about -14 LUFS, so mastering hotter only gets you turned down
(and squashed) by the platform.
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

# platform -> (integrated LUFS, true peak dBTP, loudness range)
PLATFORMS = {
    "tiktok": (-14.0, -1.0, 11.0),
    "reels": (-14.0, -1.0, 11.0),
    "instagram": (-14.0, -1.0, 11.0),
    "shorts": (-14.0, -1.0, 11.0),
    "youtube": (-14.0, -1.0, 11.0),
    "podcast": (-16.0, -1.5, 9.0),
    "broadcast": (-23.0, -2.0, 7.0),
}

# voice presets: (highpass Hz, denoise nr dB, gate threshold, comp ratio,
#                 de-ess gain dB, presence gain dB)
VOICE_PRESETS = {
    "studio": (75, 8, 0.008, 2.5, -3.0, 2.0),    # already clean mic
    "ugc": (95, 14, 0.015, 3.5, -4.5, 3.0),      # phone / avatar / room tone
    "noisy": (110, 22, 0.030, 4.5, -5.0, 3.5),   # street, aircon, laptop mic
    "off": None,
}


def _db_to_lin(db):
    return 10.0 ** (float(db) / 20.0)


def voice_chain(preset="ugc", limit_db=-1.0):
    """Return the -af chain for dialogue cleanup (empty string if disabled)."""
    cfg = VOICE_PRESETS.get(preset, VOICE_PRESETS["ugc"])
    if cfg is None:
        return ""
    hp, nr, gate, ratio, deess, presence = cfg
    return ",".join([
        f"highpass=f={hp}",                       # kill rumble / handling noise
        f"afftdn=nr={nr}:nf=-28:tn=1",            # adaptive broadband denoise
        f"agate=threshold={gate}:ratio=2:attack=8:release=260:knee=4",
        f"equalizer=f=250:t=q:w=1.2:g=-2",        # unmud the low-mids
        f"equalizer=f=6800:t=q:w=1.8:g={deess}",  # de-esser
        f"equalizer=f=3200:t=q:w=1.4:g={presence}",  # presence / intelligibility
        f"acompressor=threshold=-18dB:ratio={ratio}:attack=12:release=200:"
        f"knee=6:makeup=2",
        f"alimiter=limit={_db_to_lin(limit_db):.4f}:level=disabled",
    ])


def measure(path, target=-14.0, tp=-1.0, lra=11.0):
    """Pass 1: EBU R128 measurement via loudnorm's JSON report."""
    ops.require_file(path, "audio input")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
           f"loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json",
           "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not blocks:
        raise RuntimeError("loudnorm measurement failed:\n" + err[-1200:])
    data = json.loads(blocks[-1])
    return {k: (float(v) if _isnum(v) else v) for k, v in data.items()}


def _isnum(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def loudnorm_filter(path, platform="reels", linear=True):
    """Build the pass-2 loudnorm filter using measured values."""
    target, tp, lra = PLATFORMS.get(platform, PLATFORMS["reels"])
    m = measure(path, target, tp, lra)
    parts = [
        f"loudnorm=I={target}:TP={tp}:LRA={lra}",
        f"measured_I={m.get('input_i', -24.0)}",
        f"measured_TP={m.get('input_tp', -6.0)}",
        f"measured_LRA={m.get('input_lra', 7.0)}",
        f"measured_thresh={m.get('input_thresh', -34.0)}",
        f"offset={m.get('target_offset', 0.0)}",
        f"linear={'true' if linear else 'false'}",
        "print_format=summary",
    ]
    return ":".join(parts), m


def normalize(src, dst, platform="reels", linear=True):
    """Two-pass loudness normalization + true-peak safety limiter."""
    ops.require_file(src, "input")
    if not ops._has_audio(src):
        raise ops.InputError(f"no audio stream in {src}")
    _, tp, _ = PLATFORMS.get(platform, PLATFORMS["reels"])
    lf, m = loudnorm_filter(src, platform, linear)
    af = f"{lf},alimiter=limit={_db_to_lin(tp):.4f}:level=disabled,aresample=48000"
    vcopy = ["-c:v", "copy"] if ops._dims(src) != (0, 0) else []
    ops.run(["-i", src, *vcopy, "-af", af, *ops.AAC, "-ar", "48000", dst])
    return dst, m


def voice(src, dst, preset="ugc", platform=None):
    """Dialogue cleanup, optionally followed by loudness normalization."""
    ops.require_file(src, "input")
    if not ops._has_audio(src):
        raise ops.InputError(f"no audio stream in {src}")
    chain = voice_chain(preset)
    if not chain:
        chain = "anull"
    vcopy = ["-c:v", "copy"] if ops._dims(src) != (0, 0) else []
    tmp = dst
    if platform:
        tmp = os.path.join(tempfile.mkdtemp(prefix="voice_"),
                           "voiced" + os.path.splitext(dst)[1])
    ops.run(["-i", src, *vcopy, "-af", chain, *ops.AAC, "-ar", "48000", tmp])
    if platform:
        normalize(tmp, dst, platform)
    return dst


def music_bed(video, music, dst, gain_db=-16.0, duck=True, loop=True,
              fade_in=0.5, fade_out=1.2, platform="reels", carve=True,
              duck_depth="medium"):
    """Music under dialogue, done properly.

    - the bed is EQ-carved around the voice band instead of just being quiet
    - ducking uses a fast-but-musical sidechain (8 ms attack / 320 ms release)
      so the music breathes back between phrases instead of pumping
    - the master gets a true-peak limiter, then platform loudness normalization
    """
    ops.require_file(video, "video")
    ops.require_file(music, "music")
    vdur = ops._dur(video)
    has_voice = ops._has_audio(video)
    depth = {"light": (0.06, 5, 420), "medium": (0.035, 8, 320),
             "heavy": (0.02, 12, 260)}.get(duck_depth, (0.035, 8, 320))
    thr, ratio, release = depth

    tmp = tempfile.mkdtemp(prefix="musicpro_")
    mixed = os.path.join(tmp, "mixed.mp4")

    args = ["-i", video]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", music]

    bed = [f"volume={gain_db}dB"]
    if carve:
        # carve a pocket where speech lives so the voice stays intelligible
        bed += ["equalizer=f=2400:t=q:w=1.6:g=-5",
                "equalizer=f=400:t=q:w=1.2:g=-2"]
    bed += [f"afade=t=in:st=0:d={fade_in}"]
    if fade_out > 0 and vdur > fade_out:
        bed += [f"afade=t=out:st={max(0.0, vdur - fade_out)}:d={fade_out}"]
    bed_chain = ",".join(bed)

    if has_voice and duck:
        fc = (f"[0:a]asplit=2[voice][key];"
              f"[1:a]{bed_chain}[bed];"
              f"[bed][key]sidechaincompress=threshold={thr}:ratio={ratio}:"
              f"attack=8:release={release}:makeup=1:level_sc=1[duck];"
              f"[voice][duck]amix=inputs=2:duration=first:dropout_transition=0,"
              f"alimiter=limit=0.9441:level=disabled,aresample=48000[a]")
    elif has_voice:
        fc = (f"[1:a]{bed_chain}[bed];"
              f"[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0,"
              f"alimiter=limit=0.9441:level=disabled,aresample=48000[a]")
    else:
        fc = f"[1:a]{bed_chain},alimiter=limit=0.9441:level=disabled,aresample=48000[a]"

    ops.run([*args, "-filter_complex", fc, "-map", "0:v:0", "-map", "[a]",
             "-c:v", "copy", *ops.AAC, "-t", f"{vdur}", mixed])

    if platform:
        normalize(mixed, dst, platform)
    else:
        os.replace(mixed, dst)
    return dst


def master(src, dst, preset="ugc", platform="reels"):
    """Voice chain + loudness normalization in one call."""
    return voice(src, dst, preset=preset, platform=platform)


def build_parser():
    p = argparse.ArgumentParser(description="Professional audio chain")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("measure"); s.add_argument("src")
    s.add_argument("--platform", default="reels", choices=sorted(PLATFORMS))

    s = sub.add_parser("voice"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--preset", default="ugc", choices=sorted(VOICE_PRESETS))
    s.add_argument("--platform", default=None, choices=sorted(PLATFORMS))

    s = sub.add_parser("normalize"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--platform", default="reels", choices=sorted(PLATFORMS))
    s.add_argument("--dynamic", action="store_true",
                   help="non-linear (dynamic) normalization instead of linear")

    s = sub.add_parser("music"); s.add_argument("video"); s.add_argument("music")
    s.add_argument("dst")
    s.add_argument("--gain-db", type=float, default=-16.0)
    s.add_argument("--duck-depth", default="medium",
                   choices=["light", "medium", "heavy"])
    s.add_argument("--no-duck", action="store_true")
    s.add_argument("--no-carve", action="store_true")
    s.add_argument("--no-loop", action="store_true")
    s.add_argument("--fade-in", type=float, default=0.5)
    s.add_argument("--fade-out", type=float, default=1.2)
    s.add_argument("--platform", default="reels", choices=sorted(PLATFORMS))

    s = sub.add_parser("master"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--preset", default="ugc", choices=sorted(VOICE_PRESETS))
    s.add_argument("--platform", default="reels", choices=sorted(PLATFORMS))
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "measure":
        t, tp, lra = PLATFORMS[a.platform]
        m = measure(a.src, t, tp, lra)
        print(json.dumps({"ok": True, "platform": a.platform,
                          "target_lufs": t, "measured": m}, indent=2))
    elif a.cmd == "voice":
        voice(a.src, a.dst, a.preset, a.platform)
        print(json.dumps({"ok": True, "output": a.dst, "preset": a.preset,
                          "platform": a.platform}))
    elif a.cmd == "normalize":
        _, m = normalize(a.src, a.dst, a.platform, linear=not a.dynamic)
        print(json.dumps({"ok": True, "output": a.dst, "platform": a.platform,
                          "input_i": m.get("input_i"),
                          "target_lufs": PLATFORMS[a.platform][0]}))
    elif a.cmd == "music":
        music_bed(a.video, a.music, a.dst, gain_db=a.gain_db,
                  duck=not a.no_duck, loop=not a.no_loop, fade_in=a.fade_in,
                  fade_out=a.fade_out, platform=a.platform,
                  carve=not a.no_carve, duck_depth=a.duck_depth)
        print(json.dumps({"ok": True, "output": a.dst,
                          "duration_sec": round(ops._dur(a.dst), 3)}))
    else:
        master(a.src, a.dst, a.preset, a.platform)
        print(json.dumps({"ok": True, "output": a.dst}))


if __name__ == "__main__":
    main()
