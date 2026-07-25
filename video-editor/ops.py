#!/usr/bin/env python3
"""ops.py - atomic video-editing operations built on FFmpeg.

Importable functions + a CLI. Every function returns the output path.
Run `python3 ops.py <op> -h` for per-op help.

Ops: trim, concat, reframe, speed, volume, extract-audio, replace-audio,
     music, overlay-image, overlay-video, text, subtitles, transition,
     fade, export.

Design notes:
- normalize_clip() is the workhorse: it trims + fits to a target WxH/fps and
  GUARANTEES a 48k stereo AAC audio track (silent if the source had none), so
  clips from different cameras/platforms concat and cross-fade cleanly.
- Positions use FFmpeg expressions so they scale to any resolution.
- Burned-in text goes through libass (this FFmpeg build has no drawtext).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile

VENC_V = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "192k"]

# Set VIDEO_EDITOR_VERBOSE=1 to echo every ffmpeg command to stderr.
VERBOSE = os.environ.get("VIDEO_EDITOR_VERBOSE") == "1"


class InputError(SystemExit):
    """Raised (and printed cleanly) when an input file is missing/unusable."""


def require_file(path, what="input"):
    """Fail fast with a readable message instead of a raw ffmpeg error."""
    if not path:
        raise InputError(f"{what}: no path given")
    if not os.path.exists(path):
        raise InputError(f"{what} not found: {path}")
    if os.path.isdir(path):
        raise InputError(f"{what} is a directory, not a file: {path}")
    if os.path.getsize(path) == 0:
        raise InputError(f"{what} is empty: {path}")
    return path

# ----------------------------------------------------------------------------
# low-level helpers
# ----------------------------------------------------------------------------

def run(args):
    """Run ffmpeg with the given args (list). Raises on failure."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    if VERBOSE:
        print("+ " + " ".join(shlex.quote(a) for a in cmd), file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n  " + " ".join(shlex.quote(a) for a in cmd)
            + "\n" + r.stderr[-2000:]
        )
    return r


def _ffprobe(args):
    r = subprocess.run(["ffprobe", "-v", "quiet"] + args, capture_output=True, text=True)
    return r.stdout.strip()


def _dur(path):
    out = _ffprobe(["-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(out)
    except Exception:
        return 0.0


def _dims(path):
    out = _ffprobe(["-select_streams", "v:0", "-show_entries",
                    "stream=width,height", "-of", "csv=s=x:p=0", path])
    try:
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return 0, 0


def _has_audio(path):
    out = _ffprobe(["-select_streams", "a", "-show_entries",
                    "stream=index", "-of", "csv=p=0", path])
    return bool(out.strip())


def _even(n):
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


def scale_pad(w, h, mode="fit"):
    """Filter chain that fits a source into WxH. mode: fit|fill|stretch."""
    if mode == "fill":
        core = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    elif mode == "stretch":
        core = f"scale={w}:{h}"
    else:  # fit / letterbox
        core = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    return core + ",setsar=1"


def _pos_overlay(position, m):
    P = {
        "top-left": (f"{m}", f"{m}"),
        "top": ("(W-w)/2", f"{m}"),
        "top-right": (f"W-w-{m}", f"{m}"),
        "left": (f"{m}", "(H-h)/2"),
        "center": ("(W-w)/2", "(H-h)/2"),
        "right": (f"W-w-{m}", "(H-h)/2"),
        "bottom-left": (f"{m}", f"H-h-{m}"),
        "bottom": ("(W-w)/2", f"H-h-{m}"),
        "bottom-right": (f"W-w-{m}", f"H-h-{m}"),
    }
    return P.get(position, P["top-right"])


def _pos_text(position, m):
    P = {
        "top": ("(w-text_w)/2", f"{m}"),
        "top-left": (f"{m}", f"{m}"),
        "top-right": (f"w-text_w-{m}", f"{m}"),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "bottom": ("(w-text_w)/2", f"h-text_h-{m}"),
        "bottom-left": (f"{m}", f"h-text_h-{m}"),
        "bottom-right": (f"w-text_w-{m}", f"h-text_h-{m}"),
    }
    return P.get(position, P["bottom"])


def _atempo_chain(factor):
    parts = []
    x = float(factor)
    while x > 2.0:
        parts.append("atempo=2.0")
        x /= 2.0
    while x < 0.5:
        parts.append("atempo=0.5")
        x *= 2.0
    parts.append(f"atempo={x:.6f}")
    return ",".join(parts)


def _enable(start, end):
    if end is not None:
        return f":enable='between(t,{start},{end})'"
    if start and float(start) > 0:
        return f":enable='gte(t,{start})'"
    return ""


# ----------------------------------------------------------------------------
# core operations
# ----------------------------------------------------------------------------

def normalize_clip(src, dst, w, h, fps, tin=0.0, tout=None, mode="fit"):
    """Trim [tin,tout], fit to WxH/fps, guarantee 48k stereo audio."""
    d = _dur(src)
    start = float(tin or 0.0)
    end = float(tout) if tout is not None else d
    seg = max(0.02, end - start)
    vf = scale_pad(w, h, mode) + f",fps={fps},format=yuv420p"
    if _has_audio(src):
        args = ["-ss", f"{start}", "-i", src, "-t", f"{seg}",
                "-vf", vf, "-ar", "48000", "-ac", "2",
                *VENC_V, *AAC, dst]
    else:
        args = ["-ss", f"{start}", "-i", src,
                "-f", "lavfi", "-t", f"{seg}", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0", "-t", f"{seg}",
                "-vf", vf, *VENC_V, *AAC, "-shortest", dst]
    run(args)
    return dst


def trim(src, dst, start=0.0, end=None, copy=False):
    args = ["-ss", f"{start}", "-i", src]
    if end is not None:
        args += ["-t", f"{max(0.02, float(end) - float(start))}"]
    if copy:
        args += ["-c", "copy"]
    else:
        args += [*VENC_V, "-c:a", "aac", "-b:a", "192k"] if _has_audio(src) else [*VENC_V, "-an"]
    args += [dst]
    run(args)
    return dst


def concat(inputs, dst, w=None, h=None, fps=30, mode="fit"):
    if w is None or h is None:
        w0, h0 = _dims(inputs[0])
        w = w or w0
        h = h or h0
    tmp = tempfile.mkdtemp(prefix="concat_")
    norm = []
    for i, src in enumerate(inputs):
        nf = os.path.join(tmp, f"n{i}.mp4")
        normalize_clip(src, nf, w, h, fps, mode=mode)
        norm.append(nf)
    args = []
    for nf in norm:
        args += ["-i", nf]
    n = len(norm)
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    fc = f"{streams}concat=n={n}:v=1:a=1[v][a]"
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", *VENC_V, *AAC, dst]
    run(args)
    return dst


def reframe(src, dst, w, h, mode="fit", fps=None):
    fps = fps or 30
    return normalize_clip(src, dst, w, h, fps, mode=mode)


def change_speed(src, dst, factor):
    vf = f"setpts=PTS/{factor}"
    if _has_audio(src):
        af = _atempo_chain(factor)
        run(["-i", src, "-vf", vf, "-af", af, *VENC_V, *AAC, dst])
    else:
        run(["-i", src, "-vf", vf, *VENC_V, "-an", dst])
    return dst


def set_volume(src, dst, gain_db=None, mute=False):
    af = "volume=0" if mute else f"volume={gain_db}dB"
    run(["-i", src, "-c:v", "copy", "-af", af, *AAC, dst])
    return dst


def extract_audio(src, dst):
    run(["-i", src, "-vn", dst])
    return dst


def replace_audio(video, audio, dst):
    run(["-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", *AAC, "-shortest", dst])
    return dst


def mix_music(video, music, dst, gain_db=-18.0, duck=True, loop=True, fade_out=0.0):
    vdur = _dur(video)
    vhas = _has_audio(video)
    args = ["-i", video]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", music]
    if vhas and duck:
        fc = (f"[1:a]volume={gain_db}dB[m];"
              f"[m][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[mk];"
              f"[0:a][mk]amix=inputs=2:duration=first:dropout_transition=0[mix]")
    elif vhas:
        fc = (f"[1:a]volume={gain_db}dB[m];"
              f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[mix]")
    else:
        fc = f"[1:a]volume={gain_db}dB[mix]"
    if fade_out and fade_out > 0:
        fc += f";[mix]afade=t=out:st={max(0, vdur - fade_out)}:d={fade_out}[a]"
        alabel = "[a]"
    else:
        alabel = "[mix]"
    args += ["-filter_complex", fc, "-map", "0:v:0", "-map", alabel,
             "-t", f"{vdur}", "-c:v", "copy", *AAC, dst]
    run(args)
    return dst


def overlay_image(video, image, dst, position="top-right", scale=0.15,
                  start=0.0, end=None, opacity=1.0):
    W, H = _dims(video)
    ow = _even(max(2, W * float(scale)))
    m = _even(W * 0.03)
    x, y = _pos_overlay(position, m)
    fc = (f"[1:v]format=rgba,colorchannelmixer=aa={opacity},scale={ow}:-1[ov];"
          f"[0:v][ov]overlay={x}:{y}{_enable(start, end)}[v]")
    aud = ["-map", "0:a?", "-c:a", "copy"] if _has_audio(video) else []
    run(["-i", video, "-i", image, "-filter_complex", fc,
         "-map", "[v]", *aud, *VENC_V, dst])
    return dst


def overlay_video(base, ov, dst, position="bottom-right", scale=0.3,
                  start=0.0, end=None):
    W, H = _dims(base)
    ow = _even(max(2, W * float(scale)))
    m = _even(W * 0.03)
    x, y = _pos_overlay(position, m)
    fc = (f"[1:v]scale={ow}:-1[ov];"
          f"[0:v][ov]overlay={x}:{y}{_enable(start, end)}[v]")
    aud = ["-map", "0:a?", "-c:a", "copy"] if _has_audio(base) else []
    run(["-i", base, "-i", ov, "-filter_complex", fc, "-map", "[v]", *aud, *VENC_V, dst])
    return dst


def _parse_color(spec):
    spec = str(spec)
    alpha = 1.0
    if "@" in spec:
        name, a = spec.split("@", 1)
        try:
            alpha = float(a)
        except ValueError:
            alpha = 1.0
    else:
        name = spec
    return name.strip(), alpha


_NAMED_RGB = {
    "white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 224, 0),
    "red": (235, 40, 40), "green": (0, 180, 60), "blue": (30, 110, 255),
    "orange": (255, 150, 0), "cyan": (0, 220, 220), "magenta": (230, 0, 200),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
}


def _ass_color(spec):
    """Return an ASS &HAABBGGRR colour string. Accepts names, #RRGGBB, and
    an optional @alpha (1.0 opaque .. 0.0 transparent)."""
    name, alpha = _parse_color(spec)
    if name.startswith("#") and len(name) >= 7:
        r, g, b = int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16)
    else:
        r, g, b = _NAMED_RGB.get(name.lower(), (255, 255, 255))
    a = max(0, min(255, int(round((1.0 - alpha) * 255))))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    cs = int(round(max(0.0, float(t)) * 100))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


_ASS_ALIGN = {
    "bottom-left": 1, "bottom": 2, "bottom-right": 3,
    "left": 4, "center": 5, "right": 6,
    "top-left": 7, "top": 8, "top-right": 9,
}


def build_ass_text(text, start, end, w, h, position="bottom", fontsize=48,
                   fontcolor="white", box=True, boxcolor="black@0.5",
                   boxborderw=16, fontname="DejaVu Sans"):
    """Write a one-event styled ASS caption and return its path.

    FFmpeg 7's static build ships without the drawtext filter (it now needs
    libharfbuzz), so all burned-in text goes through libass, which also gives
    better typography and positioning than drawtext did.
    """
    align = _ASS_ALIGN.get(position, 2)
    mh, mv = int(w * 0.04), int(h * 0.06)
    primary = _ass_color(fontcolor)
    if box:
        border_style, outline, back = 3, max(1, round(boxborderw / 4)), _ass_color(boxcolor)
    else:
        border_style, outline, back = 1, 2, _ass_color("black@1.0")
    txt = str(text).replace("{", "(").replace("}", ")").replace("\n", "\\N")
    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{fontname},{fontsize},{primary},&H000000FF,&H00000000,{back},0,0,0,0,100,100,0,0,{border_style},{outline},0,{align},{mh},{mh},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{txt}
"""
    f = tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def draw_text(video, dst, text, position="bottom", start=0.0, end=None,
              fontsize=48, fontcolor="white", box=True, boxcolor="black@0.5",
              boxborderw=16, fontname="DejaVu Sans"):
    w, h = _dims(video)
    dur = _dur(video)
    e = float(end) if end is not None else dur
    ass_path = build_ass_text(text, start, e, w, h, position, fontsize,
                              fontcolor, box, boxcolor, boxborderw, fontname)
    vf = f"subtitles=filename='{_esc_path(ass_path)}'"
    aud = ["-c:a", "copy"] if _has_audio(video) else ["-an"]
    run(["-i", video, "-vf", vf, *VENC_V, *aud, dst])
    os.unlink(ass_path)
    return dst


def _esc_path(p):
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(video, srt, dst, force_style=None):
    vf = f"subtitles=filename='{_esc_path(srt)}'"
    if force_style:
        vf += f":force_style='{force_style}'"
    aud = ["-c:a", "copy"] if _has_audio(video) else ["-an"]
    run(["-i", video, "-vf", vf, *VENC_V, *aud, dst])
    return dst


def transition(a, b, dst, ttype="fade", duration=1.0, w=None, h=None, fps=30):
    if w is None or h is None:
        w0, h0 = _dims(a)
        w = w or w0
        h = h or h0
    tmp = tempfile.mkdtemp(prefix="xf_")
    na = normalize_clip(a, os.path.join(tmp, "a.mp4"), w, h, fps)
    nb = normalize_clip(b, os.path.join(tmp, "b.mp4"), w, h, fps)
    off = max(0.0, _dur(na) - duration)
    fc = (f"[0:v][1:v]xfade=transition={ttype}:duration={duration}:offset={off}[v];"
          f"[0:a][1:a]acrossfade=d={duration}[a]")
    run(["-i", na, "-i", nb, "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", *VENC_V, *AAC, dst])
    return dst


def fade(src, dst, fin=0.0, fout=0.0):
    d = _dur(src)
    vf, af = [], []
    if fin > 0:
        vf.append(f"fade=t=in:st=0:d={fin}")
        af.append(f"afade=t=in:st=0:d={fin}")
    if fout > 0:
        vf.append(f"fade=t=out:st={max(0, d - fout)}:d={fout}")
        af.append(f"afade=t=out:st={max(0, d - fout)}:d={fout}")
    args = ["-i", src, "-vf", ",".join(vf) or "null"]
    if _has_audio(src):
        args += ["-af", ",".join(af) or "anull", *VENC_V, *AAC, dst]
    else:
        args += [*VENC_V, "-an", dst]
    run(args)
    return dst


def export_preset(src, dst, preset="web_mp4", width=None, height=None, fps=None):
    web = ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", *AAC, "-movflags", "+faststart"]
    if preset == "web_mp4":
        run(["-i", src, *web, dst])
    elif preset in ("social_vertical", "social_square", "social_4x5"):
        dims = {"social_vertical": (1080, 1920), "social_square": (1080, 1080),
                "social_4x5": (1080, 1350)}[preset]
        w, h = width or dims[0], height or dims[1]
        vf = scale_pad(w, h, "fit") + ",format=yuv420p"
        run(["-i", src, "-vf", vf, *web, dst])
    elif preset == "gif":
        w = width or 480
        f = fps or 12
        pal = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        run(["-i", src, "-vf", f"fps={f},scale={w}:-1:flags=lanczos,palettegen", pal])
        run(["-i", src, "-i", pal, "-lavfi",
             f"fps={f},scale={w}:-1:flags=lanczos[x];[x][1:v]paletteuse", dst])
        os.unlink(pal)
    elif preset == "webm":
        run(["-i", src, "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
             "-c:a", "libopus", dst])
    else:
        raise ValueError(f"unknown preset: {preset}")
    return dst


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="Atomic FFmpeg video-editing ops")
    sub = p.add_subparsers(dest="op", required=True)

    s = sub.add_parser("trim"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--start", type=float, default=0.0); s.add_argument("--end", type=float)
    s.add_argument("--copy", action="store_true")

    s = sub.add_parser("concat"); s.add_argument("inputs", nargs="+"); s.add_argument("dst")
    s.add_argument("--width", type=int); s.add_argument("--height", type=int)
    s.add_argument("--fps", type=int, default=30); s.add_argument("--mode", default="fit")

    s = sub.add_parser("reframe"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--width", type=int, required=True); s.add_argument("--height", type=int, required=True)
    s.add_argument("--mode", default="fit"); s.add_argument("--fps", type=int)

    s = sub.add_parser("speed"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--factor", type=float, required=True)

    s = sub.add_parser("volume"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--gain-db", type=float); s.add_argument("--mute", action="store_true")

    s = sub.add_parser("extract-audio"); s.add_argument("src"); s.add_argument("dst")

    s = sub.add_parser("replace-audio"); s.add_argument("video"); s.add_argument("audio"); s.add_argument("dst")

    s = sub.add_parser("music"); s.add_argument("video"); s.add_argument("music"); s.add_argument("dst")
    s.add_argument("--gain-db", type=float, default=-18.0); s.add_argument("--no-duck", action="store_true")
    s.add_argument("--no-loop", action="store_true"); s.add_argument("--fade-out", type=float, default=0.0)

    s = sub.add_parser("overlay-image"); s.add_argument("video"); s.add_argument("image"); s.add_argument("dst")
    s.add_argument("--position", default="top-right"); s.add_argument("--scale", type=float, default=0.15)
    s.add_argument("--start", type=float, default=0.0); s.add_argument("--end", type=float)
    s.add_argument("--opacity", type=float, default=1.0)

    s = sub.add_parser("overlay-video"); s.add_argument("base"); s.add_argument("ov"); s.add_argument("dst")
    s.add_argument("--position", default="bottom-right"); s.add_argument("--scale", type=float, default=0.3)
    s.add_argument("--start", type=float, default=0.0); s.add_argument("--end", type=float)

    s = sub.add_parser("text"); s.add_argument("video"); s.add_argument("dst"); s.add_argument("--text", required=True)
    s.add_argument("--position", default="bottom"); s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float); s.add_argument("--fontsize", type=int, default=48)
    s.add_argument("--fontcolor", default="white"); s.add_argument("--no-box", action="store_true")

    s = sub.add_parser("subtitles"); s.add_argument("video"); s.add_argument("srt"); s.add_argument("dst")
    s.add_argument("--force-style")

    s = sub.add_parser("transition"); s.add_argument("a"); s.add_argument("b"); s.add_argument("dst")
    s.add_argument("--type", default="fade"); s.add_argument("--duration", type=float, default=1.0)
    s.add_argument("--width", type=int); s.add_argument("--height", type=int); s.add_argument("--fps", type=int, default=30)

    s = sub.add_parser("fade"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--in", dest="fin", type=float, default=0.0); s.add_argument("--out", dest="fout", type=float, default=0.0)

    s = sub.add_parser("export"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--preset", default="web_mp4"); s.add_argument("--width", type=int)
    s.add_argument("--height", type=int); s.add_argument("--fps", type=int)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    op = args.op

    # Fail fast on missing inputs with a readable message.
    for attr, label in (("src", "input"), ("video", "video"), ("audio", "audio"),
                        ("music", "music"), ("image", "image"), ("base", "base video"),
                        ("ov", "overlay video"), ("srt", "subtitle file"),
                        ("a", "first clip"), ("b", "second clip")):
        val = getattr(args, attr, None)
        if isinstance(val, str):
            require_file(val, label)
    for val in (getattr(args, "inputs", None) or []):
        require_file(val, "input")
    if op == "trim":
        out = trim(args.src, args.dst, args.start, args.end, args.copy)
    elif op == "concat":
        out = concat(args.inputs, args.dst, args.width, args.height, args.fps, args.mode)
    elif op == "reframe":
        out = reframe(args.src, args.dst, args.width, args.height, args.mode, args.fps)
    elif op == "speed":
        out = change_speed(args.src, args.dst, args.factor)
    elif op == "volume":
        out = set_volume(args.src, args.dst, args.gain_db, args.mute)
    elif op == "extract-audio":
        out = extract_audio(args.src, args.dst)
    elif op == "replace-audio":
        out = replace_audio(args.video, args.audio, args.dst)
    elif op == "music":
        out = mix_music(args.video, args.music, args.dst, args.gain_db,
                        not args.no_duck, not args.no_loop, args.fade_out)
    elif op == "overlay-image":
        out = overlay_image(args.video, args.image, args.dst, args.position,
                            args.scale, args.start, args.end, args.opacity)
    elif op == "overlay-video":
        out = overlay_video(args.base, args.ov, args.dst, args.position,
                            args.scale, args.start, args.end)
    elif op == "text":
        out = draw_text(args.video, args.dst, args.text, args.position, args.start,
                        args.end, args.fontsize, args.fontcolor, not args.no_box)
    elif op == "subtitles":
        out = burn_subtitles(args.video, args.srt, args.dst, args.force_style)
    elif op == "transition":
        out = transition(args.a, args.b, args.dst, args.type, args.duration,
                         args.width, args.height, args.fps)
    elif op == "fade":
        out = fade(args.src, args.dst, args.fin, args.fout)
    elif op == "export":
        out = export_preset(args.src, args.dst, args.preset, args.width, args.height, args.fps)
    else:
        raise SystemExit(f"unknown op {op}")
    print(json.dumps({"ok": True, "op": op, "output": out,
                      "duration_sec": round(_dur(out), 3)}))


if __name__ == "__main__":
    main()
