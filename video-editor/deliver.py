#!/usr/bin/env python3
"""deliver.py - platform-correct final encodes (block G, export half).

The old export was a single-pass CRF 20 `veryfast` H.264 with no colour tags
and no GOP control. Platforms re-encode whatever you upload, so an under-spec
master loses a second generation of quality. These profiles are built to
survive that re-encode:

  - CRF 18-19 with the `slow` x264 preset (visually lossless at social bitrates)
  - high profile / level 4.1, GOP fixed to 2 s, no scene-cut keyframes
  - capped VBV so uploads never spike past the platform's accepted bitrate
  - BT.709 tagging + yuv420p, so colour is interpreted correctly everywhere
  - AAC-LC 320 kb/s at 48 kHz, `+faststart` for instant playback
  - ProRes 422 HQ master profile for archival / re-editing

Ops:
  specs                  -> the profile table as JSON
  export IN OUT --profile reels
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

# width, height, fps, crf, maxrate kbps, bufsize kbps, audio kbps, max seconds
PROFILES = {
    "reels": dict(w=1080, h=1920, fps=30, crf=18, maxrate=14000, buf=28000,
                  abr="320k", max_sec=90, note="Instagram Reels 9:16"),
    "tiktok": dict(w=1080, h=1920, fps=30, crf=18, maxrate=16000, buf=32000,
                   abr="320k", max_sec=600, note="TikTok 9:16"),
    "shorts": dict(w=1080, h=1920, fps=30, crf=18, maxrate=16000, buf=32000,
                   abr="320k", max_sec=180, note="YouTube Shorts 9:16"),
    "square": dict(w=1080, h=1080, fps=30, crf=18, maxrate=12000, buf=24000,
                   abr="320k", max_sec=None, note="Feed 1:1"),
    "feed_4x5": dict(w=1080, h=1350, fps=30, crf=18, maxrate=12000, buf=24000,
                     abr="320k", max_sec=None, note="Instagram feed 4:5"),
    "youtube_hd": dict(w=1920, h=1080, fps=30, crf=18, maxrate=20000, buf=40000,
                       abr="320k", max_sec=None, note="YouTube 1080p"),
    "youtube_4k": dict(w=3840, h=2160, fps=30, crf=17, maxrate=60000, buf=120000,
                       abr="384k", max_sec=None, note="YouTube 2160p"),
    "master_prores": dict(w=None, h=None, fps=None, crf=None, maxrate=None,
                          buf=None, abr=None, max_sec=None,
                          note="ProRes 422 HQ archival master"),
}

BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709",
         "-color_trc", "bt709", "-color_range", "tv"]


def _scale_chain(w, h, fps, mode="fill"):
    core = ops.scale_pad(w, h, mode)
    chain = f"{core},format=yuv420p"
    if fps:
        chain += f",fps={fps}"
    return chain


def export(src, dst, profile="reels", mode="fill", width=None, height=None,
           fps=None, crf=None, two_pass=False, hevc=False):
    ops.require_file(src, "input")
    if profile not in PROFILES:
        raise ops.InputError(f"unknown profile: {profile}")
    p = dict(PROFILES[profile])
    w = width or p["w"]
    h = height or p["h"]
    f = fps or p["fps"] or 30

    if profile == "master_prores":
        args = ["-i", src, "-c:v", "prores_ks", "-profile:v", "3",
                "-pix_fmt", "yuv422p10le", "-vendor", "apl0",
                "-c:a", "pcm_s16le", "-ar", "48000", *BT709, dst]
        ops.run(args)
        return dst, {"profile": profile}

    gop = int(round(f * 2))
    vcodec = "libx265" if hevc else "libx264"
    venc = ["-c:v", vcodec, "-preset", "slow",
            "-crf", str(crf if crf is not None else p["crf"]),
            "-pix_fmt", "yuv420p",
            "-g", str(gop), "-keyint_min", str(gop)]
    if not hevc:
        venc += ["-profile:v", "high", "-level", "4.1", "-sc_threshold", "0",
                 "-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0"]
    else:
        venc += ["-tag:v", "hvc1"]
    if p["maxrate"]:
        venc += ["-maxrate", f"{p['maxrate']}k", "-bufsize", f"{p['buf']}k"]

    aenc = ["-c:a", "aac", "-b:a", p["abr"] or "256k", "-ar", "48000", "-ac", "2"]
    if not ops._has_audio(src):
        aenc = ["-an"]

    vf = _scale_chain(w, h, f, mode)
    common = ["-i", src, "-vf", vf, *venc, *aenc, *BT709,
              "-movflags", "+faststart"]

    if two_pass and not hevc:
        passlog = os.path.join(os.path.dirname(os.path.abspath(dst)), ".x264pass")
        bitrate = f"{int((p['maxrate'] or 12000) * 0.65)}k"
        ops.run(["-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "slow",
                 "-b:v", bitrate, "-pass", "1", "-passlogfile", passlog,
                 "-an", "-f", "mp4", os.devnull])
        ops.run(["-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "slow",
                 "-b:v", bitrate, "-maxrate", f"{p['maxrate']}k",
                 "-bufsize", f"{p['buf']}k", "-pass", "2",
                 "-passlogfile", passlog, "-g", str(gop), "-keyint_min", str(gop),
                 *aenc, *BT709, "-movflags", "+faststart", dst])
        for ext in ("-0.log", "-0.log.mbtree", ".log", ".log.mbtree"):
            try:
                os.remove(passlog + ext)
            except OSError:
                pass
    else:
        ops.run([*common, dst])

    return dst, {"profile": profile, "size": [w, h], "fps": f,
                 "codec": vcodec, "gop": gop}


def main(argv=None):
    p = argparse.ArgumentParser(description="Platform-correct final encodes")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("specs")

    s = sub.add_parser("export"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--profile", default="reels", choices=sorted(PROFILES))
    s.add_argument("--mode", default="fill", choices=["fit", "fill", "stretch"])
    s.add_argument("--width", type=int); s.add_argument("--height", type=int)
    s.add_argument("--fps", type=int); s.add_argument("--crf", type=int)
    s.add_argument("--two-pass", action="store_true")
    s.add_argument("--hevc", action="store_true")

    a = p.parse_args(argv)
    if a.cmd == "specs":
        print(json.dumps({"ok": True, "profiles": PROFILES}, indent=2))
        return
    _, info = export(a.src, a.dst, a.profile, a.mode, a.width, a.height,
                     a.fps, a.crf, a.two_pass, a.hevc)
    print(json.dumps({"ok": True, "output": a.dst,
                      "duration_sec": round(ops._dur(a.dst), 3),
                      "size_bytes": os.path.getsize(a.dst), **info}, indent=2))


if __name__ == "__main__":
    main()
