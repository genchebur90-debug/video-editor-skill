#!/usr/bin/env python3
"""render_timeline.py - render a declarative JSON timeline into a finished video.

Usage:
    python3 render_timeline.py TIMELINE.json

This is the centerpiece of the skill: an agent describes the edit as structured
JSON and this renders the montage. It leans on ops.py for every primitive.

Timeline schema (all sections optional except clips):
{
  "output": {"path": "out.mp4", "width": 1920, "height": 1080, "fps": 30,
             "preset": "web_mp4", "mode": "fit"},
  "clips": [
    {"src": "a.mp4", "in": 0, "out": 5},
    {"src": "b.mp4", "in": 2, "out": 9,
     "transition": {"type": "fade", "duration": 0.75}}
  ],
  "overlays": [
    {"type": "image", "src": "logo.png", "position": "top-right",
     "scale": 0.12, "start": 0, "end": 9, "opacity": 0.9},
    {"type": "text", "text": "Hook line", "position": "bottom",
     "start": 0, "end": 2.5, "fontsize": 64}
  ],
  "audio": [
    {"src": "music.mp3", "gain_db": -18, "duck": true, "loop": true, "fade_out": 1.5}
  ],
  "subtitles": {"src": "subs.srt", "force_style": "Fontsize=22,Outline=2"}
}

Stages run in order (clips -> join -> overlays -> audio bed -> subtitles ->
export), writing intermediate files to a temp dir. Staged on purpose:
correctness and debuggability over one giant filtergraph.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402


def join_clips(norm_files, transitions, tmp):
    """Progressive pairwise join: xfade when a clip declares a transition,
    hard-cut concat otherwise. Returns (path, duration)."""
    acc = norm_files[0]
    accdur = ops._dur(acc)
    for i in range(1, len(norm_files)):
        nxt = norm_files[i]
        tr = transitions[i]
        out = os.path.join(tmp, f"join_{i}.mp4")
        if tr:
            d = float(tr.get("duration", 0.75))
            ttype = tr.get("type", "fade")
            off = max(0.0, accdur - d)
            fc = (f"[0:v][1:v]xfade=transition={ttype}:duration={d}:offset={off}[v];"
                  f"[0:a][1:a]acrossfade=d={d}[a]")
            ops.run(["-i", acc, "-i", nxt, "-filter_complex", fc,
                     "-map", "[v]", "-map", "[a]", *ops.VENC_V, *ops.AAC, out])
            accdur = accdur + ops._dur(nxt) - d
        else:
            fc = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
            ops.run(["-i", acc, "-i", nxt, "-filter_complex", fc,
                     "-map", "[v]", "-map", "[a]", *ops.VENC_V, *ops.AAC, out])
            accdur = accdur + ops._dur(nxt)
        acc = out
    return acc, accdur


def render(timeline_path):
    with open(timeline_path) as f:
        tl = json.load(f)

    out = tl.get("output", {})
    W = int(out.get("width", 1920))
    H = int(out.get("height", 1080))
    FPS = int(out.get("fps", 30))
    preset = out.get("preset", "web_mp4")
    mode = out.get("mode", "fit")
    dst = out.get("path", "timeline_out.mp4")

    clips = tl.get("clips", [])
    if not clips:
        raise ValueError("timeline has no clips")

    tmp = tempfile.mkdtemp(prefix="timeline_")
    base = os.path.dirname(os.path.abspath(timeline_path))

    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(base, p)

    # 1) normalize every clip to the output spec
    norm_files, transitions = [], []
    for i, c in enumerate(clips):
        nf = os.path.join(tmp, f"norm_{i}.mp4")
        ops.normalize_clip(resolve(c["src"]), nf, W, H, FPS,
                           tin=c.get("in", 0) or 0, tout=c.get("out"),
                           mode=c.get("mode", mode))
        norm_files.append(nf)
        transitions.append(c.get("transition"))

    # 2) join
    if len(norm_files) == 1:
        cur = norm_files[0]
    else:
        cur, _ = join_clips(norm_files, transitions, tmp)

    # 3) overlays
    for j, ov in enumerate(tl.get("overlays", [])):
        o = os.path.join(tmp, f"ov_{j}.mp4")
        t = ov.get("type")
        if t == "image":
            ops.overlay_image(cur, resolve(ov["src"]), o,
                              position=ov.get("position", "top-right"),
                              scale=ov.get("scale", 0.15),
                              start=ov.get("start", 0.0), end=ov.get("end"),
                              opacity=ov.get("opacity", 1.0))
        elif t == "text":
            ops.draw_text(cur, o, ov["text"],
                          position=ov.get("position", "bottom"),
                          start=ov.get("start", 0.0), end=ov.get("end"),
                          fontsize=ov.get("fontsize", 48),
                          fontcolor=ov.get("fontcolor", "white"),
                          box=ov.get("box", True),
                          boxcolor=ov.get("boxcolor", "black@0.5"))
        elif t == "video":
            ops.overlay_video(cur, resolve(ov["src"]), o,
                              position=ov.get("position", "bottom-right"),
                              scale=ov.get("scale", 0.3),
                              start=ov.get("start", 0.0), end=ov.get("end"))
        else:
            raise ValueError(f"unknown overlay type: {t}")
        cur = o

    # 4) audio bed(s)
    for k, a in enumerate(tl.get("audio", [])):
        o = os.path.join(tmp, f"aud_{k}.mp4")
        ops.mix_music(cur, resolve(a["src"]), o,
                      gain_db=a.get("gain_db", -18.0),
                      duck=a.get("duck", True), loop=a.get("loop", True),
                      fade_out=a.get("fade_out", 0.0))
        cur = o

    # 5) subtitles
    subs = tl.get("subtitles")
    if subs:
        o = os.path.join(tmp, "subs.mp4")
        src = subs["src"] if isinstance(subs, dict) else subs
        style = subs.get("force_style") if isinstance(subs, dict) else None
        ops.burn_subtitles(cur, resolve(src), o, force_style=style)
        cur = o

    # 6) export
    if not os.path.isabs(dst):
        dst = os.path.join(base, dst)
    ops.export_preset(cur, dst, preset=preset)
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 render_timeline.py TIMELINE.json", file=sys.stderr)
        sys.exit(2)
    result = render(sys.argv[1])
    print(json.dumps({"ok": True, "output": result,
                      "duration_sec": round(ops._dur(result), 3)}))
