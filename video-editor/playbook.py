#!/usr/bin/env python3
"""playbook.py - one-command PROFESSIONAL editorial playbooks.

Raw clips + a short brief in, a broadcast-quality, platform-ready video out.
The pipeline runs the same stages a real post workflow does, in the order a
professional would run them:

  1. tighten        remove dead air (analyze.py)
  2. polish         stabilize / denoise / sharpen (polish.py)
  3. reframe        subject-tracking 9:16 reframe, not a dumb centre crop
                    (reframe_smart.py)
  4. colour         HDR tone map + auto correction + look (color.py)
  5. motion         duration-aware push-in
  6. assembly       concat, optionally with beat-snapped cut lengths (rhythm.py)
  7. dialogue       denoise / de-ess / compress / EQ (audio_pro.py)
  8. graphics       hook title, animated captions with safe zones (captions.py)
  9. music          EQ-carved, sidechain-ducked bed (audio_pro.py)
 10. loudness       two-pass EBU R128 to the platform target
 11. delivery       CRF 18 slow, 2 s GOP, BT.709, faststart (deliver.py)
 12. QC             automatic pre-publish report (qc.py)

Modes differ only in editorial DEFAULTS (override anything):
  short : Reels/Shorts/TikTok - word-highlight captions (centre), punchy pace
  ad    : promo/product       - clean captions (bottom), balanced pace
  ugc   : talking-head avatar - pop captions (bottom safe zone), voice-first

Adaptive rule: if you PROVIDE captions/transcript, dead-air trimming is OFF by
default (so caption timing stays in sync); otherwise it is ON.

Examples:
  python3 playbook.py short --clips a.mp4 b.mp4 --hook "WATCH THIS" \\
          --captions words.json --music music.mp3 --platform tiktok --out s.mp4
  python3 playbook.py ugc --clips avatar.mp4 --transcript-text "..." \\
          --look clean --platform reels --qc --out ugc.mp4
  python3 playbook.py ad --clips product.mp4 --music bed.mp3 --look teal_orange \\
          --beat-sync --two-pass --out ad.mp4
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops            # noqa: E402
import captions       # noqa: E402
import analyze        # noqa: E402
import audio_pro      # noqa: E402
import color as color_mod  # noqa: E402
import deliver        # noqa: E402
import polish as polish_mod  # noqa: E402
import qc as qc_mod   # noqa: E402
import reframe_smart  # noqa: E402
import rhythm         # noqa: E402

FPS = 30

DEFAULTS = {
    "short": {"caption_style": "tiktok", "energy": "punchy",
              "music_gain": -17.0, "caption_position": "center",
              "look": "punch", "voice": "ugc"},
    "ad": {"caption_style": "clean", "energy": "balanced",
           "music_gain": -15.0, "caption_position": "bottom",
           "look": "teal_orange", "voice": "studio"},
    "ugc": {"caption_style": "pop", "energy": "punchy",
            "music_gain": -20.0, "caption_position": "bottom",
            "look": "clean", "voice": "ugc"},
}

# Default CTA copy per language. Pass --cta to override, --cta "" to disable.
CTA_TEXT = {
    "en": {"short": "Follow for more", "ad": "Order now", "ugc": "Link in bio"},
    "ru": {"short": "Подпишись", "ad": "Закажи сейчас", "ugc": "Ссылка в шапке"},
}
DEFAULT_LANG = os.environ.get("VIDEO_EDITOR_LANG", "en").lower()
if DEFAULT_LANG not in CTA_TEXT:
    DEFAULT_LANG = "en"

PLATFORM_PROFILE = {"reels": "reels", "tiktok": "tiktok", "shorts": "shorts",
                    "youtube": "youtube_hd"}


def punch_in(src, dst, w, h, maxz=1.12):
    """Slow push-in. The zoom step is derived from the clip duration so the
    move finishes exactly at the end of the clip instead of hitting maxz early
    (and then freezing) on long takes."""
    pre = int(round(1.6 * w))
    dur = max(0.2, ops._dur(src))
    step = max(0.00005, (maxz - 1.0) / max(1.0, dur * FPS))
    vf = (f"scale={pre}:-1,zoompan=z='min(zoom+{step:.6f},{maxz})':d=1:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={FPS},format=yuv420p")
    aud = ops.AAC if ops._has_audio(src) else ["-an"]
    ops.run(["-i", src, "-vf", vf, *ops.VENC_V, *aud, dst])
    return dst


def cta_card(text, dst, w, h, accent, dur=1.6, logo=None):
    tmp = tempfile.mkdtemp(prefix="cta_")
    base = os.path.join(tmp, "base.mp4")
    ops.run(["-f", "lavfi", "-i", f"color=c=0x0A0A0A:s={w}x{h}:r={FPS}:d={dur}",
             "-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=48000:cl=stereo",
             "-shortest", *ops.VENC_V, *ops.AAC, base])
    cur = base
    if logo and os.path.exists(logo):
        l = os.path.join(tmp, "logo.mp4")
        ops.overlay_image(cur, logo, l, position="top", scale=0.35, opacity=1.0)
        cur = l
    ops.draw_text(cur, dst, text, position="center", fontsize=int(h * 0.075),
                  fontcolor="white", box=True, boxcolor=f"{accent}@0.9")
    return dst


def _load_words(captions_src, transcript_text, duration):
    if captions_src:
        if captions_src.endswith(".srt"):
            return captions.words_from_segments(captions.parse_srt(captions_src))
        data = json.load(open(captions_src, encoding="utf-8"))
        if any(" " in str(d.get("text", "")) for d in data):
            return captions.words_from_segments(data)
        return [{"text": d["text"], "start": float(d["start"]), "end": float(d["end"])}
                for d in data]
    if transcript_text:
        return captions.words_from_text(transcript_text, duration)
    return None


def make(mode, clips, out, hook=None, captions_src=None, caption_style=None,
         transcript_text=None, music=None, cta=None, accent="#00E5FF",
         energy=None, w=1080, h=1920, music_gain=None, logo=None,
         tighten=None, normalize_audio=True, hook_dur=2.0, cta_dur=1.6,
         caption_position=None, lang=None, dry_run=False,
         platform="reels", look=None, lut=None, smart_reframe=True,
         stabilize=None, denoise="off", voice=None, beat_sync=False,
         two_pass=False, run_qc=False, pro=True):
    d = DEFAULTS[mode]
    lang = (lang or DEFAULT_LANG).lower()
    if lang not in CTA_TEXT:
        lang = "en"
    caption_style = caption_style or d["caption_style"]
    energy = energy or d["energy"]
    cta = CTA_TEXT[lang][mode] if cta is None else cta
    music_gain = d["music_gain"] if music_gain is None else music_gain
    caption_position = caption_position or d["caption_position"]
    look = (look if look is not None else d["look"]) if pro else "none"
    voice = (voice if voice is not None else d["voice"]) if pro else "off"

    if not clips:
        raise ops.InputError("no clips given")
    for c in clips:
        ops.require_file(c, "clip")
    if captions_src:
        ops.require_file(captions_src, "captions file")
    if music:
        ops.require_file(music, "music file")
    if logo:
        ops.require_file(logo, "logo file")
    if lut:
        ops.require_file(lut, "LUT file")
    out_dir = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(out_dir):
        raise ops.InputError(f"output folder does not exist: {out_dir}")

    has_caps = bool(captions_src or transcript_text)
    do_tighten = (not has_caps) if tighten is None else tighten
    profile = PLATFORM_PROFILE.get(platform, "reels")

    if dry_run:
        stages = []
        for c in clips:
            n = os.path.basename(c)
            if do_tighten:
                stages.append(f"tighten (remove dead air): {n}")
            if pro and (stabilize is not False or denoise != "off"):
                stages.append(f"polish (stabilize={stabilize}, denoise={denoise}): {n}")
            stages.append(
                f"{'subject-aware' if (pro and smart_reframe) else 'centre'} "
                f"reframe {w}x{h}: {n}")
            if pro and (look != "none" or lut):
                stages.append(f"colour grade (look={look}, lut={bool(lut)}): {n}")
            if energy == "punchy":
                stages.append(f"punch-in: {n}")
        if beat_sync and music:
            stages.append("beat detection + beat-snapped cut lengths")
        if len(clips) > 1:
            stages.append(f"concat {len(clips)} clips")
        if pro and voice != "off":
            stages.append(f"dialogue chain (preset={voice})")
        if hook:
            stages.append(f"hook text ({hook_dur}s): {hook}")
        if has_caps:
            stages.append(f"captions: style={caption_style} pos={caption_position} "
                          f"safe={platform}")
        if cta:
            stages.append(f"CTA end-card ({cta_dur}s): {cta}")
        if music:
            stages.append(f"ducked music bed {music_gain} dB (EQ-carved)")
        if normalize_audio:
            stages.append(f"two-pass loudness to {platform} target")
        stages.append(f"deliver profile={profile} two_pass={two_pass} -> {out}")
        if run_qc:
            stages.append("QC report")
        return {"dry_run": True, "mode": mode, "lang": lang, "pro": pro,
                "platform": platform, "stages": stages}

    tmp = tempfile.mkdtemp(prefix=f"pb_{mode}_")
    report = {"stages": []}

    # ---- beat-aware pacing -------------------------------------------------
    plan = None
    if beat_sync and music:
        plan = rhythm.plan(clips, music, energy=energy)
        report["bpm"] = plan["bpm"]
        report["stages"].append(f"beat sync @ {plan['bpm']} BPM")

    processed = []
    for i, clip in enumerate(clips):
        cur = clip
        if plan:
            seg = plan["segments"][i]
            b = os.path.join(tmp, f"b{i}.mp4")
            ops.trim(cur, b, seg["in"], seg["in"] + seg["out"])
            cur = b
        if do_tighten and ops._has_audio(cur):
            t = os.path.join(tmp, f"t{i}.mp4")
            analyze.tighten(cur, t)
            cur = t
        if pro and (stabilize is not False or denoise != "off"):
            pl = os.path.join(tmp, f"pl{i}.mp4")
            _, pinfo = polish_mod.auto(cur, pl, do_stabilize=stabilize,
                                       denoise_level=denoise,
                                       sharpen_amount=0.4)
            if pinfo["applied"]:
                report["stages"].append(f"polish {i}: {', '.join(pinfo['applied'])}")
            cur = pl
        r = os.path.join(tmp, f"r{i}.mp4")
        if pro and smart_reframe:
            _, rinfo = reframe_smart.reframe(cur, r, w, h, anchor="motion",
                                             safe=platform if platform in
                                             reframe_smart.SAFE_ZONES else "reels")
            report["stages"].append(
                f"smart reframe {i}: anchor={rinfo['anchor']} drift={rinfo['drift']}")
        else:
            ops.reframe(cur, r, w, h, mode="fill")
        cur = r
        if pro and (look != "none" or lut):
            g = os.path.join(tmp, f"g{i}.mp4")
            color_mod.grade(cur, g, look=look, auto=True, lut=lut, sharpen=0.0)
            cur = g
        if energy == "punchy":
            p = os.path.join(tmp, f"p{i}.mp4")
            punch_in(cur, p, w, h)
            cur = p
        processed.append(cur)

    if len(processed) == 1:
        base = processed[0]
    else:
        base = os.path.join(tmp, "base.mp4")
        ops.concat(processed, base, w, h, FPS, mode="fill")
    cur = base

    # ---- dialogue ----------------------------------------------------------
    if pro and voice != "off" and ops._has_audio(cur):
        v = os.path.join(tmp, "voice.mp4")
        audio_pro.voice(cur, v, preset=voice)
        cur = v
        report["stages"].append(f"dialogue chain ({voice})")
    elif normalize_audio and ops._has_audio(cur) and not pro:
        info = analyze.analyze(cur)
        g = info.get("suggested_gain_db", 0.0) or 0.0
        if abs(g) > 1.0:
            n = os.path.join(tmp, "norm.mp4")
            ops.set_volume(cur, n, gain_db=g)
            cur = n

    if hook:
        hk = os.path.join(tmp, "hook.mp4")
        ops.draw_text(cur, hk, hook, position="top", start=0.0,
                      end=min(hook_dur, ops._dur(cur)), fontsize=int(h * 0.058),
                      fontcolor="white", box=True, boxcolor="black@0.55")
        cur = hk

    words = _load_words(captions_src, transcript_text, ops._dur(cur))
    if words:
        safe = platform if platform in captions.SAFE_ZONES else "reels"
        ass = captions.build_ass(words, w=w, h=h, style=caption_style,
                                 accent=accent, position=caption_position,
                                 safe=safe, animate=pro, emphasis=pro)
        cap = os.path.join(tmp, "cap.mp4")
        ops.burn_subtitles(cur, ass, cap)
        os.unlink(ass)
        cur = cap
        report["stages"].append(f"captions ({caption_style}, safe={safe})")

    if cta:
        card = os.path.join(tmp, "card.mp4")
        cta_card(cta, card, w, h, accent, dur=cta_dur, logo=logo)
        full = os.path.join(tmp, "full.mp4")
        ops.concat([cur, card], full, w, h, FPS, mode="fill")
        cur = full

    # ---- music + loudness --------------------------------------------------
    if music and os.path.exists(music):
        m = os.path.join(tmp, "music.mp4")
        if pro:
            audio_pro.music_bed(cur, music, m, gain_db=music_gain, duck=True,
                                loop=True, fade_out=1.2,
                                platform=platform if normalize_audio else None)
        else:
            ops.mix_music(cur, music, m, gain_db=music_gain, duck=True,
                          loop=True, fade_out=1.0)
        cur = m
        report["stages"].append("ducked music bed")
    elif pro and normalize_audio and ops._has_audio(cur):
        n = os.path.join(tmp, "loud.mp4")
        audio_pro.normalize(cur, n, platform=platform)
        cur = n
        report["stages"].append(f"loudness -> {platform} target")

    # ---- delivery ----------------------------------------------------------
    if pro:
        deliver.export(cur, out, profile=profile, width=w, height=h,
                       fps=FPS, two_pass=two_pass)
        report["stages"].append(f"delivery profile {profile}")
    else:
        ops.export_preset(cur, out, preset="social_vertical", width=w, height=h)

    if run_qc:
        report["qc"] = qc_mod.report(out, platform if platform in qc_mod.TARGETS
                                     else "reels")
    return out, report


def build_parser():
    p = argparse.ArgumentParser(description="Professional editorial playbooks")
    sub = p.add_subparsers(dest="mode", required=True)
    for mode in ("short", "ad", "ugc"):
        s = sub.add_parser(mode)
        s.add_argument("--clips", nargs="+", required=True)
        s.add_argument("--out", required=True)
        s.add_argument("--hook")
        s.add_argument("--captions")
        s.add_argument("--caption-style",
                       choices=["tiktok", "pop", "karaoke", "box", "clean"])
        s.add_argument("--caption-position", choices=["center", "bottom", "top"])
        s.add_argument("--transcript-text")
        s.add_argument("--music")
        s.add_argument("--cta")
        s.add_argument("--accent", default="#00E5FF")
        s.add_argument("--energy", choices=["punchy", "balanced", "minimal"])
        s.add_argument("--width", type=int, default=1080)
        s.add_argument("--height", type=int, default=1920)
        s.add_argument("--music-gain", type=float)
        s.add_argument("--logo")
        s.add_argument("--hook-dur", type=float, default=2.0)
        s.add_argument("--cta-dur", type=float, default=1.6)
        s.add_argument("--lang", choices=sorted(CTA_TEXT), default=None,
                       help="language for default CTA copy (default: en, "
                            "or $VIDEO_EDITOR_LANG)")
        s.add_argument("--dry-run", action="store_true",
                       help="print the planned stages as JSON, render nothing")
        # --- professional pipeline controls (all ON by default) ---
        s.add_argument("--platform", default="reels",
                       choices=sorted(PLATFORM_PROFILE),
                       help="loudness target, safe zones and export profile")
        s.add_argument("--look", choices=sorted(color_mod.LOOKS),
                       help="colour look (default depends on mode)")
        s.add_argument("--lut", help="apply a .cube LUT before the look")
        s.add_argument("--voice", choices=sorted(audio_pro.VOICE_PRESETS),
                       help="dialogue cleanup preset")
        s.add_argument("--denoise", default="off",
                       choices=["off", "light", "medium", "heavy"])
        s.add_argument("--beat-sync", action="store_true",
                       help="snap cut lengths to the music's beat grid")
        s.add_argument("--two-pass", action="store_true",
                       help="two-pass VBR export (slower, best for long cuts)")
        s.add_argument("--qc", dest="run_qc", action="store_true",
                       help="run the pre-publish QC report on the result")
        s.add_argument("--basic", dest="pro", action="store_false",
                       help="disable the pro pipeline (fast draft render)")
        g = s.add_mutually_exclusive_group()
        g.add_argument("--stabilize", dest="stabilize", action="store_true", default=None)
        g.add_argument("--no-stabilize", dest="stabilize", action="store_false")
        g2 = s.add_mutually_exclusive_group()
        g2.add_argument("--smart-reframe", dest="smart_reframe",
                        action="store_true", default=True)
        g2.add_argument("--no-smart-reframe", dest="smart_reframe",
                        action="store_false")
        g3 = s.add_mutually_exclusive_group()
        g3.add_argument("--tighten", dest="tighten", action="store_true", default=None)
        g3.add_argument("--no-tighten", dest="tighten", action="store_false")
        s.add_argument("--no-normalize", dest="normalize_audio", action="store_false")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    res = make(a.mode, a.clips, a.out, hook=a.hook, captions_src=a.captions,
               caption_style=a.caption_style, transcript_text=a.transcript_text,
               music=a.music, cta=a.cta, accent=a.accent, energy=a.energy,
               w=a.width, h=a.height, music_gain=a.music_gain, logo=a.logo,
               tighten=a.tighten, normalize_audio=a.normalize_audio,
               hook_dur=a.hook_dur, cta_dur=a.cta_dur,
               caption_position=a.caption_position, lang=a.lang,
               dry_run=a.dry_run, platform=a.platform, look=a.look, lut=a.lut,
               smart_reframe=a.smart_reframe, stabilize=a.stabilize,
               denoise=a.denoise, voice=a.voice, beat_sync=a.beat_sync,
               two_pass=a.two_pass, run_qc=a.run_qc, pro=a.pro)
    if isinstance(res, dict):
        print(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
        return
    out, report = res
    payload = {"ok": True, "mode": a.mode, "platform": a.platform,
               "output": out, "duration_sec": round(ops._dur(out), 3),
               "size_bytes": os.path.getsize(out), **report}
    if "qc" in payload:
        payload["qc_verdict"] = payload["qc"]["verdict"]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
