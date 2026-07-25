#!/usr/bin/env python3
"""rhythm.py - beat-synced pacing, speed ramps, J/L cuts (block D).

Even with clean audio and a good grade, an edit reads as amateur when the cuts
land randomly against the music. Editors cut ON the beat, let audio lead the
picture (J/L cuts), and ramp speed instead of hard-switching it.

Everything here is pure stdlib: the beat tracker decodes a mono PCM proxy with
ffmpeg and analyses it in Python (energy-flux onset envelope -> autocorrelation
tempo estimate -> phase-locked beat grid). No numpy, no librosa.

Ops:
  beats  MUSIC                 -> {bpm, beats[], confidence}
  plan   --clips ... --music   -> clip durations snapped to musical phrases
  ramp   SRC DST --ramp t:factor ...   -> speed ramps with pitch-safe audio
  freeze SRC DST --at T --dur D        -> freeze-frame accent
  jlcut  A B DST --lead 0.4            -> audio of B starts before its picture
"""
import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

SR = 22050
HOP = 512


def _pcm_mono(path, sr=SR):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
           "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("audio decode failed: " + r.stderr.decode()[-600:])
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) - (len(r.stdout) % 2)])
    return a


def onset_envelope(samples, sr=SR, hop=HOP):
    """Half-wave rectified energy flux - a cheap but effective onset detector."""
    n = len(samples) // hop
    energy = [0.0] * n
    for i in range(n):
        base = i * hop
        acc = 0
        for j in range(base, base + hop, 4):      # decimate: 4x faster, same shape
            v = samples[j]
            acc += v * v
        energy[i] = math.sqrt(acc / max(1, hop / 4))
    env = [0.0] * n
    for i in range(1, n):
        env[i] = max(0.0, energy[i] - energy[i - 1])
    peak = max(env) if env else 0.0
    if peak > 0:
        env = [v / peak for v in env]
    return env


def estimate_tempo(env, sr=SR, hop=HOP, bpm_min=60.0, bpm_max=190.0):
    """Autocorrelation of the onset envelope -> (bpm, confidence)."""
    if len(env) < 16:
        return 0.0, 0.0
    fps = sr / float(hop)
    lag_min = max(1, int(round(fps * 60.0 / bpm_max)))
    lag_max = min(len(env) - 1, int(round(fps * 60.0 / bpm_min)))
    best_lag, best_score, total = 0, 0.0, 0.0
    for lag in range(lag_min, lag_max + 1):
        acc = 0.0
        for i in range(lag, len(env)):
            acc += env[i] * env[i - lag]
        acc /= (len(env) - lag)
        total += acc
        if acc > best_score:
            best_score, best_lag = acc, lag
    if not best_lag:
        return 0.0, 0.0
    bpm = 60.0 * fps / best_lag
    while bpm < bpm_min:
        bpm *= 2
    while bpm > bpm_max:
        bpm /= 2
    mean = total / max(1, (lag_max - lag_min + 1))
    confidence = round(min(1.0, (best_score / mean - 1.0) / 2.0), 3) if mean else 0.0
    return round(bpm, 2), max(0.0, confidence)


def beat_grid(env, bpm, sr=SR, hop=HOP, duration=None):
    """Lock a constant-tempo grid to the strongest phase in the envelope."""
    if bpm <= 0:
        return []
    fps = sr / float(hop)
    period = 60.0 / bpm * fps
    best_phase, best_score = 0.0, -1.0
    steps = max(1, int(round(period)))
    for p in range(steps):
        score, k = 0.0, 0
        while True:
            idx = int(round(p + k * period))
            if idx >= len(env):
                break
            # a small window tolerates human timing in live-played music
            score += max(env[max(0, idx - 1):min(len(env), idx + 2)] or [0.0])
            k += 1
        if score > best_score:
            best_score, best_phase = score, p
    beats, k = [], 0
    dur = duration if duration is not None else len(env) / fps
    while True:
        t = (best_phase + k * period) / fps
        if t > dur:
            break
        beats.append(round(t, 3))
        k += 1
    return beats


def beats(music, bpm_hint=None):
    ops.require_file(music, "music")
    samples = _pcm_mono(music)
    env = onset_envelope(samples)
    dur = len(samples) / float(SR)
    if bpm_hint:
        bpm, conf = float(bpm_hint), 1.0
    else:
        bpm, conf = estimate_tempo(env)
    grid = beat_grid(env, bpm, duration=dur)
    return {"bpm": bpm, "confidence": conf, "duration_sec": round(dur, 3),
            "beats": grid, "bars": grid[::4]}


def snap(times, grid, max_shift=0.35):
    """Snap arbitrary cut times to the nearest beat within max_shift."""
    out = []
    for t in times:
        if not grid:
            out.append(round(t, 3))
            continue
        nearest = min(grid, key=lambda b: abs(b - t))
        out.append(round(nearest if abs(nearest - t) <= max_shift else t, 3))
    return out


def plan(clips, music=None, target=None, energy="punchy", bpm_hint=None,
         beats_per_cut=None):
    """Give every clip a duration that lands its out-point on a beat.

    Pacing curve: short-form retention needs a fast opening, then breathing
    room, so early clips get fewer beats than later ones.
    """
    info = beats(music, bpm_hint) if music else {"bpm": 0, "beats": [], "confidence": 0}
    bpm = info["bpm"] or 120.0
    beat = 60.0 / bpm
    per = beats_per_cut or {"punchy": 2, "balanced": 4, "minimal": 8}.get(energy, 4)
    curve = {"punchy": [0.5, 0.75, 1.0, 1.0, 1.25],
             "balanced": [0.75, 1.0, 1.0, 1.25],
             "minimal": [1.0]}.get(energy, [1.0])

    segments, t = [], 0.0
    for i, clip in enumerate(clips):
        clip_dur = ops._dur(clip) if os.path.exists(clip) else 0.0
        mult = curve[min(i, len(curve) - 1)]
        want = max(beat, round(per * beat * mult / beat) * beat)
        if clip_dur:
            want = min(want, clip_dur)
        segments.append({"src": clip, "in": 0.0, "out": round(want, 3),
                         "cut_at": round(t + want, 3)})
        t += want
    if target and t > 0:
        scale = float(target) / t
        for s in segments:
            s["out"] = round(s["out"] * scale, 3)
    cut_times = snap([s["cut_at"] for s in segments], info["beats"])
    prev = 0.0
    for s, ct in zip(segments, cut_times):
        s["cut_at"] = ct
        s["out"] = round(max(0.3, ct - prev), 3)
        prev = ct
    return {"bpm": bpm, "beat_sec": round(beat, 4),
            "confidence": info["confidence"], "total_sec": round(prev, 3),
            "segments": segments}


def ramp(src, dst, ramps, fps=30):
    """Speed ramps: list of (start_sec, factor). Segments are rendered and
    joined, so audio stays pitch-correct via atempo."""
    ops.require_file(src, "input")
    dur = ops._dur(src)
    pts = sorted([(max(0.0, float(t)), float(f)) for t, f in ramps])
    if not pts or pts[0][0] > 0:
        pts.insert(0, (0.0, 1.0))
    tmp = tempfile.mkdtemp(prefix="ramp_")
    parts = []
    for i, (start, factor) in enumerate(pts):
        end = pts[i + 1][0] if i + 1 < len(pts) else dur
        if end - start < 0.05:
            continue
        seg = os.path.join(tmp, f"seg{i}.mp4")
        cut = os.path.join(tmp, f"cut{i}.mp4")
        ops.trim(src, cut, start, end)
        if abs(factor - 1.0) < 0.01:
            parts.append(cut)
        else:
            ops.change_speed(cut, seg, factor)
            parts.append(seg)
    if len(parts) == 1:
        os.replace(parts[0], dst)
        return dst
    w, h = ops._dims(src)
    ops.concat(parts, dst, w, h, fps, mode="fill")
    return dst


def freeze(src, dst, at, dur=0.6, fps=30):
    """Freeze-frame accent (the classic 'hit' on a punchline or product shot)."""
    ops.require_file(src, "input")
    total = ops._dur(src)
    at = max(0.05, min(total - 0.05, float(at)))
    tmp = tempfile.mkdtemp(prefix="freeze_")
    head = os.path.join(tmp, "head.mp4")
    tail = os.path.join(tmp, "tail.mp4")
    still = os.path.join(tmp, "still.png")
    hold = os.path.join(tmp, "hold.mp4")
    ops.trim(src, head, 0, at)
    ops.trim(src, tail, at, total)
    ops.run(["-ss", f"{at}", "-i", src, "-frames:v", "1", still])
    w, h = ops._dims(src)
    ops.run(["-loop", "1", "-t", f"{dur}", "-i", still,
             "-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=48000:cl=stereo",
             "-vf", f"scale={w}:{h},fps={fps},format=yuv420p",
             *ops.VENC_V, *ops.AAC, "-shortest", hold])
    ops.concat([head, hold, tail], dst, w, h, fps, mode="fill")
    return dst


def jlcut(a, b, dst, lead=0.4, kind="j", fps=30):
    """J-cut: audio of clip B starts before its picture.
    L-cut: audio of clip A continues over the picture of B."""
    ops.require_file(a, "first clip")
    ops.require_file(b, "second clip")
    w, h = ops._dims(a)
    da, db = ops._dur(a), ops._dur(b)
    lead = max(0.05, min(lead, da - 0.1, db - 0.1))
    tmp = tempfile.mkdtemp(prefix="jl_")
    joined = os.path.join(tmp, "joined.mp4")
    ops.concat([a, b], joined, w, h, fps, mode="fill")
    total = ops._dur(joined)
    if kind == "j":
        # pull B's audio earlier under the tail of A
        fc = (f"[1:a]adelay={int(max(0, (da - lead)) * 1000)}|"
              f"{int(max(0, (da - lead)) * 1000)}[bd];"
              f"[0:a][bd]amix=inputs=2:duration=first:dropout_transition=0[a]")
        ops.run(["-i", joined, "-i", b, "-filter_complex", fc,
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", *ops.AAC,
                 "-t", f"{total}", dst])
    else:
        # let A's audio run past the picture cut
        fc = (f"[1:a]atrim=start={max(0.0, da - lead)},asetpts=PTS-STARTPTS,"
              f"adelay={int(da * 1000)}|{int(da * 1000)}[ad];"
              f"[0:a][ad]amix=inputs=2:duration=first:dropout_transition=0[a]")
        ops.run(["-i", joined, "-i", a, "-filter_complex", fc,
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", *ops.AAC,
                 "-t", f"{total}", dst])
    return dst


def _parse_ramps(values):
    out = []
    for v in values or []:
        t, _, f = str(v).partition(":")
        out.append((float(t), float(f)))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Beat-synced pacing and time effects")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("beats"); s.add_argument("music")
    s.add_argument("--bpm", type=float, help="skip detection, use this BPM")

    s = sub.add_parser("plan")
    s.add_argument("--clips", nargs="+", required=True)
    s.add_argument("--music")
    s.add_argument("--energy", default="punchy", choices=["punchy", "balanced", "minimal"])
    s.add_argument("--target", type=float, help="target total duration in seconds")
    s.add_argument("--bpm", type=float)
    s.add_argument("--beats-per-cut", type=int)

    s = sub.add_parser("ramp"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--ramp", nargs="+", required=True,
                   help="START:FACTOR pairs, e.g. 0:1.0 2.5:1.6 4:0.5")
    s.add_argument("--fps", type=int, default=30)

    s = sub.add_parser("freeze"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--at", type=float, required=True)
    s.add_argument("--dur", type=float, default=0.6)

    s = sub.add_parser("jlcut"); s.add_argument("a"); s.add_argument("b")
    s.add_argument("dst"); s.add_argument("--lead", type=float, default=0.4)
    s.add_argument("--kind", default="j", choices=["j", "l"])

    a = p.parse_args(argv)
    if a.cmd == "beats":
        info = beats(a.music, a.bpm)
        info["beats"] = info["beats"][:64]
        print(json.dumps({"ok": True, **info}, indent=2))
    elif a.cmd == "plan":
        print(json.dumps({"ok": True, **plan(a.clips, a.music, a.target,
                                             a.energy, a.bpm, a.beats_per_cut)},
                         indent=2))
    elif a.cmd == "ramp":
        ramp(a.src, a.dst, _parse_ramps(a.ramp), a.fps)
        print(json.dumps({"ok": True, "output": a.dst,
                          "duration_sec": round(ops._dur(a.dst), 3)}))
    elif a.cmd == "freeze":
        freeze(a.src, a.dst, a.at, a.dur)
        print(json.dumps({"ok": True, "output": a.dst,
                          "duration_sec": round(ops._dur(a.dst), 3)}))
    else:
        jlcut(a.a, a.b, a.dst, a.lead, a.kind)
        print(json.dumps({"ok": True, "output": a.dst, "kind": a.kind,
                          "duration_sec": round(ops._dur(a.dst), 3)}))


if __name__ == "__main__":
    main()
