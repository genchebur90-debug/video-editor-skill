#!/usr/bin/env python3
"""morph.py - audio-first morph montage: one continuous voice, many personas.

The format this solves: a presenter talks without a break while their look,
render style or world changes every few seconds. Most attempts fail because
people ask the video model to "transform and keep talking" - each clip is
generated separately, so the speech falls apart.

The fix is to invert the order. THE VOICE IS THE SPINE:

  1. one continuous VO exists first (recorded, or a cloned voice)
  2. the script is split into phrases; one phrase = one persona
  3. phrase boundaries are found in the REAL audio (pauses), not guessed
  4. boundaries are snapped to the musical beat
  5. each persona clip is cut to fit its slot, and the VO is laid over the top

The ear glues the picture together: while the voice never breaks, the viewer
reads every visual change as a device rather than a glitch. A cut that misses
the beat reads as a mistake, which is why step 4 is not optional.

Ops:
  plan      --vo VO --script s.json [--music M]  -> timed assembly plan
  assemble  PLAN --out cut.mp4                   -> rendered montage + VO
  subs      PLAN --out subs.srt                  -> SRT straight from the plan
  seams                                          -> available seam styles

Pure stdlib + ffmpeg, like the rest of the skill. No pip installs.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402
import ops  # noqa: E402
import rhythm  # noqa: E402

# Seam styles. A seam hides the cut; it is NOT a decorative transition.
# Durations are deliberately tiny - a long dissolve kills short-form energy.
SEAMS = {
    "hard":  {"xfade": None,        "dur": 0.00,
              "note": "butt splice. Default. Use when the action carries the cut"},
    "whip":  {"xfade": "hblur",     "dur": 0.12,
              "note": "horizontal blur smear - reads as a whip pan"},
    "slide": {"xfade": "slideleft", "dur": 0.14,
              "note": "frame pushed out of the way, good after a hand swipe"},
    "flash": {"xfade": "fadewhite", "dur": 0.10,
              "note": "white flash. Strongest beat marker, use sparingly"},
    "black": {"xfade": "fadeblack", "dur": 0.10,
              "note": "hard blink to black, punctuates a section"},
    "pixel": {"xfade": "pixelize",  "dur": 0.16,
              "note": "digital break-up, fits render-style changes"},
    "morph": {"xfade": "dissolve",  "dur": 0.30,
              "note": "true blend. ONLY where you generated a real morph pair"},
}

MIN_SEG = 0.60          # below this a persona cannot register at all
IDEAL_LO, IDEAL_HI = 2.5, 4.0   # per-persona seconds that read as a device


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def _pause_centres(vo, noise_db=-30.0, min_d=0.18):
    """Centres of the gaps between speech - the natural places to cut."""
    dur = ops._dur(vo)
    segs = analyze.speech_segments(vo, noise_db=noise_db, min_d=min_d,
                                   pad=0.0, min_seg=0.10)
    centres, prev_end = [], None
    for s, e in segs:
        if prev_end is not None and s > prev_end:
            centres.append(round((prev_end + s) / 2.0, 3))
        prev_end = e
    return centres, segs, dur


def _load_phrases(script=None, phrase=None):
    """Phrases from a script JSON or from repeated --phrase flags."""
    if script:
        ops.require_file(script, "script")
        with open(script, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("phrases", data) if isinstance(data, dict) else data
        out = []
        for it in items:
            out.append(dict(it) if isinstance(it, dict) else {"text": str(it)})
        return out
    return [{"text": t} for t in (phrase or [])]


def _weighted_bounds(total_dur, phrases):
    """Fallback split: proportional to how much text each phrase carries."""
    weights = [max(1, len((p.get("text") or p.get("subtitle") or "").strip()))
               for p in phrases]
    tot = float(sum(weights)) or 1.0
    bounds, acc = [], 0
    for w in weights[:-1]:
        acc += w
        bounds.append(round(total_dur * acc / tot, 3))
    return bounds


def _nearest(value, candidates, window):
    """Nearest candidate within window, else None."""
    if not candidates:
        return None
    best = min(candidates, key=lambda c: abs(c - value))
    return best if abs(best - value) <= window else None


def _monotonic(bounds, total, n_seg, min_seg=MIN_SEG):
    """Force strictly increasing boundaries with a survivable minimum."""
    out, prev = [], 0.0
    for i, b in enumerate(bounds):
        remaining = n_seg - i - 1
        hi = total - remaining * min_seg
        b = max(prev + min_seg, min(b, hi))
        out.append(round(b, 3))
        prev = b
    return out


def plan(vo, phrases, music=None, clips=None, snap="both", pause_window=0.60,
         beat_window=0.20, bpm_hint=None, default_seam="hard"):
    """Derive one timed slot per phrase from the real voice track."""
    ops.require_file(vo, "vo")
    if not phrases:
        raise ops.InputError("no phrases given (use --script or --phrase)")

    centres, speech, dur = _pause_centres(vo)
    n = len(phrases)

    explicit = all(p.get("start") is not None and p.get("end") is not None
                   for p in phrases)
    notes = []

    if explicit:
        bounds = [float(p["end"]) for p in phrases[:-1]]
        notes.append("boundaries taken from the script (explicit start/end)")
    elif len(speech) == n:
        # one detected speech run per phrase - the clean case
        bounds = [round((speech[i][1] + speech[i + 1][0]) / 2.0, 3)
                  for i in range(n - 1)]
        notes.append(f"boundaries from {n} detected speech runs")
    else:
        bounds = _weighted_bounds(dur, phrases)
        notes.append(f"{len(speech)} speech runs vs {n} phrases - "
                     "split by text weight, then snapped to pauses")

    beat_info = {"bpm": 0, "confidence": 0, "beats": []}
    if music and snap in ("beat", "both"):
        beat_info = rhythm.beats(music, bpm_hint)

    snapped = []
    for b in bounds:
        target, how = b, "raw"
        if snap in ("pause", "both"):
            c = _nearest(target, centres, pause_window)
            if c is not None:
                target, how = c, "pause"
        if snap in ("beat", "both") and beat_info["beats"]:
            k = _nearest(target, beat_info["beats"], beat_window)
            if k is not None:
                target, how = k, ("pause+beat" if how == "pause" else "beat")
        snapped.append((round(target, 3), how))

    bounds = _monotonic([s for s, _ in snapped], dur, n)
    hows = [h for _, h in snapped]

    clip_list = clips or []
    segments, prev = [], 0.0
    for i, p in enumerate(phrases):
        end = bounds[i] if i < len(bounds) else round(dur, 3)
        clip = p.get("clip") or (clip_list[i] if i < len(clip_list) else None)
        seg = {
            "index": i,
            "persona": p.get("persona") or f"look_{i + 1}",
            "text": p.get("text", ""),
            "subtitle": p.get("subtitle", ""),
            "start": round(prev, 3),
            "end": round(end, 3),
            "dur": round(end - prev, 3),
            "clip": clip,
            "seam": p.get("seam") or (default_seam if i else "hard"),
            "snapped_by": hows[i - 1] if 0 < i <= len(hows) else "start",
        }
        segments.append(seg)
        prev = end

    avg = round(sum(s["dur"] for s in segments) / float(n), 2)
    warnings = []
    if avg < IDEAL_LO:
        warnings.append(f"average {avg}s per persona is fast - below ~{IDEAL_LO}s "
                        "the changes blur into noise")
    if avg > IDEAL_HI:
        warnings.append(f"average {avg}s per persona is slow for short-form - "
                        f"aim for {IDEAL_LO}-{IDEAL_HI}s")
    if music and beat_info["confidence"] < 0.15:
        warnings.append("weak beat detection - pass --bpm or drop beat snapping")
    for s in segments:
        if not s["clip"]:
            warnings.append(f"segment {s['index']} ({s['persona']}) has no clip")
        if s["dur"] < MIN_SEG + 0.2:
            warnings.append(f"segment {s['index']} is only {s['dur']}s")

    return {
        "vo": os.path.abspath(vo),
        "music": os.path.abspath(music) if music else None,
        "total_sec": round(dur, 3),
        "count": n,
        "avg_sec": avg,
        "bpm": beat_info["bpm"],
        "beat_confidence": beat_info["confidence"],
        "speech_runs": len(speech),
        "pause_centres": centres,
        "notes": notes,
        "warnings": warnings,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def _prep(src, dst, w, h, fps, want, max_stretch=1.5, mode="fill"):
    """Cut/fit one persona clip to its slot. Video only - the VO owns audio.

    Short clip -> slow it down (up to max_stretch), then hold the last frame.
    Holding beats looping: a visible loop restart reads as a mistake.
    """
    have = ops._dur(src)
    vf = ops.scale_pad(w, h, mode) + f",fps={fps},format=yuv420p"
    action = "trim"
    if have >= want:
        ops.run(["-ss", "0", "-i", src, "-t", f"{want:.3f}",
                 "-vf", vf, *ops.VENC_V, "-an", dst])
    else:
        factor = have / want
        if factor >= 1.0 / max_stretch:
            action = f"slowed x{1 / factor:.2f}"
            ops.run(["-i", src, "-vf", f"setpts=PTS/{factor:.6f},{vf}",
                     "-t", f"{want:.3f}", *ops.VENC_V, "-an", dst])
        else:
            action = "held last frame"
            ops.run(["-i", src, "-vf", f"{vf},tpad=stop_mode=clone:stop_duration={want:.3f}",
                     "-t", f"{want:.3f}", *ops.VENC_V, "-an", dst])
    return dst, action


def _video_chain(files, seams, dst, w, h, fps):
    """Concat the persona clips, applying each seam. One pass, no requant."""
    if len(files) == 1:
        ops.run(["-i", files[0], "-c", "copy", dst])
        return dst

    use_xfade = any(SEAMS[s]["xfade"] for s in seams[1:])
    args = []
    for f in files:
        args += ["-i", f]

    if not use_xfade:
        streams = "".join(f"[{i}:v]" for i in range(len(files)))
        fc = f"{streams}concat=n={len(files)}:v=1:a=0[v]"
        args += ["-filter_complex", fc, "-map", "[v]", *ops.VENC_V, "-an", dst]
        ops.run(args)
        return dst

    parts, last, acc = [], "0:v", ops._dur(files[0])
    for i in range(1, len(files)):
        spec = SEAMS[seams[i]]
        d = spec["dur"] if spec["xfade"] else 0.0
        label = f"v{i}"
        if spec["xfade"] and d > 0:
            off = max(0.0, acc - d)
            parts.append(f"[{last}][{i}:v]xfade=transition={spec['xfade']}"
                         f":duration={d:.3f}:offset={off:.3f}[{label}]")
            acc = acc + ops._dur(files[i]) - d
        else:
            parts.append(f"[{last}][{i}:v]concat=n=2:v=1:a=0[{label}]")
            acc = acc + ops._dur(files[i])
        last = label
    fc = ";".join(parts)
    args += ["-filter_complex", fc, "-map", f"[{last}]", *ops.VENC_V, "-an", dst]
    ops.run(args)
    return dst


def assemble(plan_path, dst, width=1080, height=1920, fps=30, music=None,
             music_db=-20.0, seam=None, max_stretch=1.5, mode="fill",
             vo_db=None, picture=None):
    """Render the montage and lay the continuous VO over it.

    `picture` caches the rendered visual chain: pass the same path again to
    iterate on audio (music level, ducking) without re-rendering the picture.
    """
    ops.require_file(plan_path, "plan")
    with open(plan_path, encoding="utf-8") as fh:
        p = json.load(fh)
    segs = p.get("segments") or []
    if not segs:
        raise ops.InputError("plan has no segments")

    vo = p.get("vo")
    ops.require_file(vo, "vo (from plan)")
    music = music or p.get("music")

    tmp = tempfile.mkdtemp(prefix="morph_")
    cached = bool(picture) and os.path.exists(picture) and os.path.getsize(picture) > 0
    report = []

    if cached:
        pic = picture
        report.append({"note": "reused cached picture", "path": picture})
    else:
        files, seams = [], []
        for s in segs:
            clip = s.get("clip")
            if not clip:
                raise ops.InputError(
                    f"segment {s['index']} ({s['persona']}) has no clip")
            ops.require_file(clip, f"clip for segment {s['index']}")
            st = seam or s.get("seam") or "hard"
            if st not in SEAMS:
                raise ops.InputError(
                    f"unknown seam '{st}' (see `morph.py seams`)")
            # xfade eats its own duration, so pad the slot to keep the VO in sync
            pad = SEAMS[st]["dur"] if (SEAMS[st]["xfade"] and s["index"] > 0) else 0.0
            want = max(0.10, float(s["dur"]) + pad)
            out = os.path.join(tmp, f"s{s['index']:02d}.mp4")
            _, action = _prep(clip, out, width, height, fps, want, max_stretch, mode)
            files.append(out)
            seams.append(st)
            report.append({"index": s["index"], "persona": s["persona"],
                           "slot_sec": round(want, 3), "seam": st, "fit": action})
        pic = picture or os.path.join(tmp, "picture.mp4")
        _video_chain(files, seams, pic, width, height, fps)

    # The VO replaces everything. This is the whole trick.
    #
    # Duration is pinned explicitly to the picture: `-shortest` plus a looped
    # music bed silently truncates the mix (the bed's own length wins), which
    # cost us 1.3s of the ending the first time round.
    total = ops._dur(pic)
    vo_af = f"aresample=48000{f',volume={vo_db}dB' if vo_db else ''}"
    args = ["-i", pic, "-i", vo]
    if music:
        ops.require_file(music, "music")
        args += ["-stream_loop", "-1", "-t", f"{total:.3f}", "-i", music]
        # asplit: the VO feeds both the duck key and the mix, and an ffmpeg
        # label may only have one consumer.
        fc = (f"[1:a]{vo_af},apad,atrim=0:{total:.3f},asplit=2[vo][key];"
              f"[2:a]aresample=48000,volume={music_db}dB[bed];"
              f"[bed][key]sidechaincompress=threshold=0.03:ratio=12:attack=8:"
              f"release=260[duck];[duck][vo]amix=inputs=2:duration=longest:"
              f"dropout_transition=0,alimiter=limit=0.95,"
              f"aformat=channel_layouts=stereo[a]")
        args += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[a]"]
    else:
        fc = (f"[1:a]{vo_af},apad,atrim=0:{total:.3f},"
              f"aformat=channel_layouts=stereo[a]")
        args += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[a]"]
    args += ["-c:v", "copy", *ops.AAC, "-t", f"{total:.3f}",
             "-movflags", "+faststart", dst]
    ops.run(args)

    return {"ok": True, "op": "assemble", "output": dst,
            "duration_sec": round(ops._dur(dst), 3),
            "vo_sec": round(ops._dur(vo), 3),
            "picture": pic if picture else None,
            "picture_cached": cached,
            "segments": report,
            "next": "captions.py burn (subtitles) -> audio_pro.py normalize -> qc.py"}


# ---------------------------------------------------------------------------
# subtitles straight from the plan
# ---------------------------------------------------------------------------

def _srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def subs(plan_path, dst, field="subtitle", fallback=True):
    """Write an SRT from the plan. Timings are already correct - no ASR needed.

    This is why the plan carries both `text` (spoken) and `subtitle`
    (on-screen): Russian audio with English burned-in subtitles is one asset
    serving two markets.
    """
    ops.require_file(plan_path, "plan")
    with open(plan_path, encoding="utf-8") as fh:
        p = json.load(fh)
    lines, n = [], 0
    for s in p.get("segments", []):
        txt = (s.get(field) or "").strip()
        if not txt and fallback:
            txt = (s.get("text") or "").strip()
        if not txt:
            continue
        n += 1
        lines.append(f"{n}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{txt}\n")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return {"ok": True, "op": "subs", "output": dst, "cues": n, "field": field}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="audio-first morph montage")
    sub = p.add_subparsers(dest="op", required=True)

    s = sub.add_parser("plan", help="VO + phrases -> timed assembly plan")
    s.add_argument("--vo", required=True, help="continuous voice track (or video)")
    s.add_argument("--script", help="JSON: {phrases:[{text,subtitle,persona,clip,seam}]}")
    s.add_argument("--phrase", action="append", help="repeatable inline phrase")
    s.add_argument("--music", help="music bed, for beat snapping")
    s.add_argument("--clips", nargs="*", help="persona clips, in order")
    s.add_argument("--snap", choices=["pause", "beat", "both", "none"], default="both")
    s.add_argument("--pause-window", type=float, default=0.60)
    s.add_argument("--beat-window", type=float, default=0.20)
    s.add_argument("--bpm", type=float, dest="bpm_hint")
    s.add_argument("--seam", default="hard", help="default seam between personas")
    s.add_argument("--out", help="write plan JSON here")

    s = sub.add_parser("assemble", help="plan -> rendered montage with the VO")
    s.add_argument("plan")
    s.add_argument("--out", required=True)
    s.add_argument("--width", type=int, default=1080)
    s.add_argument("--height", type=int, default=1920)
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--music")
    s.add_argument("--music-db", type=float, default=-20.0)
    s.add_argument("--vo-db", type=float)
    s.add_argument("--seam", help="override every seam")
    s.add_argument("--max-stretch", type=float, default=1.5)
    s.add_argument("--mode", choices=["fit", "fill", "stretch"], default="fill")
    s.add_argument("--picture", help="cache/reuse the rendered visual chain here")

    s = sub.add_parser("subs", help="plan -> SRT (perfect timings, no ASR)")
    s.add_argument("plan")
    s.add_argument("--out", required=True)
    s.add_argument("--field", default="subtitle")
    s.add_argument("--no-fallback", action="store_true")

    sub.add_parser("seams", help="list seam styles")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    op = args.op

    if op == "seams":
        print(json.dumps({"ok": True, "op": "seams", "seams": SEAMS,
                          "ideal_sec_per_persona": [IDEAL_LO, IDEAL_HI]}, indent=2))
        return

    if op == "plan":
        phrases = _load_phrases(args.script, args.phrase)
        res = plan(args.vo, phrases, args.music, args.clips,
                   snap=args.snap, pause_window=args.pause_window,
                   beat_window=args.beat_window, bpm_hint=args.bpm_hint,
                   default_seam=args.seam)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False, indent=2)
            res = dict(res, output=args.out)
            res.pop("pause_centres", None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if op == "assemble":
        res = assemble(args.plan, args.out, args.width, args.height, args.fps,
                       args.music, args.music_db, args.seam,
                       args.max_stretch, args.mode, args.vo_db, args.picture)
    elif op == "subs":
        res = subs(args.plan, args.out, args.field, not args.no_fallback)
    else:
        raise SystemExit(f"unknown op {op}")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
