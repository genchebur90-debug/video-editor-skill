---
name: video-editor
description: Professional programmatic video editing and montage with FFmpeg + libass. Broadcast-grade audio (EBU R128 loudness, dialogue cleanup, sidechain ducking), subject-tracking 9:16 reframing, colour management (HDR tone mapping, auto grade, LUTs, film looks), beat-synced pacing with speed ramps and J/L cuts, audio-first morph montage (one continuous voice over changing personas, cuts snapped to real pauses and the beat), animated word-by-word captions with platform safe zones, stabilization and denoise, platform export profiles (Reels/TikTok/Shorts/YouTube) and automatic pre-publish QC. Includes one-command playbooks for short-form, ads and UGC-avatar videos. Use when editing or assembling video from existing footage. Requires ffmpeg/ffprobe on PATH (run doctor.py to verify). Not for generating AI footage or for analyzing/"watching" video content.
---

# Video Editor (FFmpeg + libass) — professional pipeline

Programmatic video editing / montage that aims at a **professional result, not
a basic render**: measured loudness instead of guessed gain, subject-aware
reframing instead of a centre crop, a real colour stage, beat-aware pacing,
animated captions inside platform safe zones, and a QC report before you
publish. Tuned for **Reels / TikTok / Shorts**, **ads** and **UGC-avatar**
content. Pure Python stdlib — no pip installs, no credentials.

## Requirements

- `ffmpeg` + `ffprobe` on PATH (libx264, aac, libass, xfade, zoompan,
  loudnorm, vidstab, zscale/tonemap for the pro stages)
- Python 3.8+
- Verify: `python3 doctor.py` (add `--smoke` to render a tiny test video)

Install ffmpeg: `brew install ffmpeg` (macOS) | `apt/dnf install ffmpeg`
(Linux) | `winget install Gyan.FFmpeg` (Windows).

Env vars: `VIDEO_EDITOR_VERBOSE=1` echoes every ffmpeg command to stderr;
`VIDEO_EDITOR_LANG=ru|en` sets the default CTA language.
End-to-end self-test: `bash ../tests/smoke.sh`.

All inputs must be local files. Every command prints a compact JSON result.

## Quick start — playbooks

One command: raw clips + brief -> finished, platform-ready video.

```bash
# SHORT (Reels/Shorts/TikTok)
python3 playbook.py short --clips a.mp4 b.mp4 \
  --hook "WATCH TO THE END" --captions words.json --music bed.mp3 \
  --platform tiktok --accent "#00E5FF" --qc --out short.mp4

# UGC avatar / talking head
python3 playbook.py ugc --clips avatar.mp4 --captions words.json \
  --platform reels --look clean --voice ugc --out ugc.mp4

# AD / promo, cuts locked to the music
python3 playbook.py ad --clips product.mp4 b-roll.mp4 --music bed.mp3 \
  --look teal_orange --beat-sync --two-pass --platform reels --out ad.mp4
```

Professional pipeline (all stages ON by default; `--basic` renders a fast draft):

1. dead-air trim -> 2. stabilize / denoise / sharpen -> 3. subject-tracking
reframe -> 4. HDR tone map + auto colour + look -> 5. duration-aware push-in ->
6. concat (optionally beat-snapped) -> 7. dialogue cleanup -> 8. hook +
animated captions -> 9. EQ-carved ducked music -> 10. two-pass loudness to the
platform target -> 11. CRF 18 / 2 s GOP / BT.709 / faststart delivery -> 12. QC.

Key flags: `--platform reels|tiktok|shorts|youtube`, `--look`, `--lut file.cube`,
`--voice studio|ugc|podcast|phone|off`, `--denoise light|medium|heavy`,
`--stabilize/--no-stabilize`, `--smart-reframe/--no-smart-reframe`,
`--beat-sync`, `--two-pass`, `--qc`, `--caption-style tiktok|pop|karaoke|box|clean`,
`--caption-position`, `--energy punchy|balanced|minimal`, `--cta "text"`,
`--logo`, `--lang en|ru`, `--dry-run`, `--basic`.

Captions source: `--captions file.srt|words.json` OR `--transcript-text "..."`.
Supplying captions turns dead-air trim OFF to keep sync.

## Scripts

| script | purpose |
|--------|---------|
| `doctor.py` | environment check (`--smoke` = test render) |
| `probe.py IN` | media info JSON (duration, res, fps, codecs, audio) |
| `analyze.py analyze\|tighten` | silences, speech, scenes, loudness / dead-air removal |
| `ops.py <op> ...` | atomic ops: trim, concat, reframe, speed, volume, extract-audio, replace-audio, music, overlay-image, overlay-video, text, subtitles, transition, fade, export |
| `audio_pro.py measure\|voice\|normalize\|music` | EBU R128 two-pass loudness, dialogue chain (de-noise, gate, de-ess, compress, EQ), true-peak limiting, sidechain ducking with EQ carve |
| `reframe_smart.py IN OUT` | subject-tracking 9:16 reframe with smoothed pan and platform safe zones (`--analyze-only` to inspect the track) |
| `color.py stats\|grade\|looks` | HDR->SDR tone map, measured auto grade, `.cube` LUTs, looks (`clean, punch, teal_orange, film, warm_ad, cold_tech, bw`), BT.709 tagging |
| `rhythm.py beats\|plan\|ramp\|freeze\|jlcut` | BPM/beat detection, beat-snapped cut plan, speed ramps, freeze accents, J/L cuts |
| `morph.py plan\|assemble\|subs\|seams` | audio-first morph montage: phrase slots derived from the real VO, snapped to pauses and beats, persona clips fitted to their slots, continuous voice laid over the top, SRT emitted straight from the plan |
| `polish.py stabilize\|denoise\|sharpen\|grain\|auto\|measure` | two-pass vidstab, hqdn3d/nlmeans, luma-only unsharp, film grain |
| `captions.py build\|burn` | animated captions (tiktok / pop / karaoke / box / clean), eased word pop, smart line breaks, emphasis word, `--safe` platform zones |
| `deliver.py export\|specs` | platform export profiles (reels, tiktok, shorts, square, feed_4x5, youtube_hd, youtube_4k, master_prores), CRF 18 slow, 2 s GOP, VBV cap, faststart, optional two-pass / HEVC |
| `qc.py OUT --platform reels` | pre-publish QC: loudness, true peak, clipping, silence, black frames, freezes, aspect/res/fps, colour tags, faststart, duration limits (exit 1 on FAIL) |
| `render_timeline.py timeline.json` | declarative montage (schema below) |
| `playbook.py short\|ad\|ugc` | one-command editorial playbooks |
| `transcribe.py IN --out words.json` | OPTIONAL ASR hook (whisperx / faster-whisper / openai-whisper / whisper.cpp) with `--separate demucs\|filter` voice isolation; `--list-backends` first |
| `localize.py translate\|dual\|burn` | FREE/local caption translation (argos offline / local Ollama LLM / self-hosted LibreTranslate), glossary protection, bilingual caption track |
| `dub.py speak\|dub\|voices` | FREE/local TTS voice-over (edge-tts / piper / espeak) time-fitted to the original cut, optional ducked original bed |
| `fetch.py URL --out clip.mp4` | optional source download via yt-dlp |

## Multi-language versions (all free, no API keys)

Same video, second language, three commands:

```bash
python3 transcribe.py cut.mp4 --out words.json --separate demucs --backend whisperx
python3 localize.py translate --words words.json --from ru --to en --out words_en.json \
  --glossary brand.json
python3 dub.py dub --video cut.mp4 --words words_en.json --lang en \
  --keep-original -18 --out cut_en.mp4
python3 audio_pro.py normalize cut_en.mp4 final_en.mp4 --platform reels
```

- `transcribe.py --backend whisperx` gives real forced-aligned word timings
  (best captions); `--separate` isolates the voice from a music bed first.
- `localize.py` never calls a paid API: `argostranslate` runs offline, `ollama`
  uses your local LLM, `libre` talks to a LibreTranslate you host. Run
  `localize.py backends` to see what is installed.
- A glossary JSON (`{"BrandName": "BrandName"}`) keeps product names intact.
- `localize.py dual` builds the bilingual caption layout (translation big,
  original dimmed underneath).
- `dub.py` renders each line, time-compresses it (pitch-preserved, capped by
  `--max-speed`) so it fits its slot, and lays the lines on the timeline;
  `--keep-original -18` keeps room tone and music sidechain-ducked under it.

## Morph montage — one voice, many looks

The format: a presenter keeps talking while their look, render style or world
changes every few seconds. Attempts fail when you ask the video model to
"transform and keep talking" — clips are generated separately, so the speech
falls apart.

Invert the order. **The voice is the spine:**

```bash
# 1. plan: slots come from the REAL voice, not from guesses
python3 morph.py plan --vo vo.wav --script script.json --music bed.mp3 \
  --bpm 120 --out plan.json

# 2. subtitles: timings are already known, so no ASR is needed
python3 morph.py subs plan.json --out subs_en.srt

# 3. assemble: persona clips fitted to slots, VO laid over the top
python3 morph.py assemble plan.json --out cut.mp4 --music bed.mp3 \
  --picture picture.mp4

# 4. finish on the normal path
python3 captions.py burn --video cut.mp4 --srt subs_en.srt --out subbed.mp4 --safe reels
python3 audio_pro.py normalize subbed.mp4 loud.mp4 --platform reels
python3 qc.py loud.mp4 --platform reels
```

`script.json` carries one entry per persona (see `examples/morph.example.json`):

```json
{"phrases": [
  {"persona": "live", "seam": "hard", "clip": "c0.mp4",
   "text": "Это видео я не снимал.",
   "subtitle": "I didn't film this."},
  {"persona": "anime", "seam": "whip", "clip": "c1.mp4",
   "text": "Меня зовут …", "subtitle": "My name is …"}
]}
```

Why it holds together:

- **Boundaries come from the audio.** `plan` runs silence detection on the VO and
  cuts in the gaps between phrases. When the detected speech-run count matches
  the phrase count it uses those gaps directly; otherwise it splits by text
  weight and snaps to the nearest pause.
- **Then the beat.** Each boundary moves to the nearest beat within
  `--beat-window` (0.2 s). A change that misses the beat reads as a glitch, not
  a device. `snapped_by` in the plan tells you what actually moved each cut.
- **Density is checked.** `plan` warns when the average slot falls outside
  2.5–4.0 s: faster blurs into noise, slower is sluggish for short-form.
- **Slots are honoured, not clips.** A clip longer than its slot is trimmed; a
  short one is slowed up to `--max-stretch` (1.5), and beyond that its last
  frame is held. `fit` in the output reports which happened per segment.
- **Seams hide cuts, they are not decoration.** `morph.py seams` lists them:
  `hard` (butt splice, the default), `whip`, `slide`, `flash`, `black`, `pixel`,
  `morph`. All are 0.10–0.30 s. An xfade consumes its own duration, so slots
  are padded to compensate and the VO stays in sync.
- **`text` vs `subtitle`.** Russian audio with English burned-in subtitles is
  one asset serving two markets: the RU viewer hears it, the EN viewer reads it.

`--picture picture.mp4` caches the rendered visual chain, so iterating on music
level or ducking costs seconds instead of a full re-render.

## Caption readability (broadcast rules)

Captions run through a Netflix-style readability pass by default: one line on
screen, hard character limit (`--max-chars`), reading-speed limit
(`--max-cps`, default 20 chars/sec) and a minimum on-screen duration. Disable
with `--no-one-line` if you want raw grouping.

## Professional defaults worth knowing

- **Loudness**: -14 LUFS integrated, -1 dBTP ceiling for all social platforms
  (two-pass `loudnorm` + `alimiter`); `--platform broadcast` targets -23 LUFS.
- **Dialogue**: high-pass 80 Hz -> spectral de-noise -> gate -> de-esser ->
  compressor (3:1) -> presence EQ. Presets: `studio, ugc, podcast, phone`.
- **Music**: bed is EQ-carved around 2.5 kHz and sidechain-ducked to the voice,
  so speech stays intelligible without riding the fader.
- **Reframe**: per-column motion/contrast energy finds the subject, keyframes
  are smoothed and interpolated, so the pan drifts instead of jittering.
- **Colour**: `signalstats` drives black/white point, white balance and
  saturation; HDR sources are tone-mapped in linear light (hable).
- **Export**: CRF 18, `slow`, high@4.1, GOP = 2 s, scene-cut keyframes off,
  VBV capped per platform, AAC 320 kb/s @ 48 kHz, `+faststart`, BT.709 tags.

## Timeline JSON

```json
{ "output": {"path":"out.mp4","width":1920,"height":1080,"fps":30,"preset":"web_mp4"},
  "clips": [ {"src":"a.mp4","in":0,"out":5},
             {"src":"b.mp4","in":2,"out":9,"transition":{"type":"fade","duration":0.75}} ],
  "overlays": [ {"type":"image","src":"logo.png","position":"top-right","scale":0.12},
                {"type":"text","text":"Hook","position":"bottom","start":0,"end":2.5,"fontsize":64} ],
  "audio": [ {"src":"music.mp3","gain_db":-18,"duck":true,"loop":true,"fade_out":1.5} ],
  "subtitles": {"src":"subs.srt","force_style":"Fontsize=22,Outline=2"} }
```

Presets: `web_mp4, social_vertical, social_square, social_4x5, gif, webm`.
Positions: `top/bottom/center/left/right` + corners. `mode`: `fit|fill|stretch`.
See `examples/`.

## Recipes

- Fix bad phone audio: `audio_pro.py voice in.mp4 out.mp4 --preset ugc --platform reels`
- Check loudness only: `audio_pro.py measure in.mp4 --platform tiktok`
- Landscape -> vertical, subject tracked: `reframe_smart.py in.mp4 out.mp4 --safe tiktok`
- Grade a flat/HDR clip: `color.py grade in.mp4 out.mp4 --look film --sharpen 0.4`
- Find the tempo: `rhythm.py beats bed.mp3` -> plan cuts: `rhythm.py plan --clips *.mp4 --music bed.mp3`
- Speed ramp: `rhythm.py ramp in.mp4 out.mp4 --ramp 0:1.0 2.5:1.8 4:0.6`
- Stabilize shaky handheld: `polish.py stabilize in.mp4 out.mp4 --smoothing 20`
- Final master: `deliver.py export cut.mp4 final.mp4 --profile tiktok --two-pass`
- Morph montage: `morph.py plan --vo vo.wav --script s.json --music bed.mp3 --out p.json` -> `morph.py assemble p.json --out cut.mp4`
- Publish check: `qc.py final.mp4 --platform tiktok`

## Motion graphics (Remotion) — optional

`remotion/` contains React compositions (`Captions`, `CTAEndCard`,
`KineticIntro`) for richer animation. Requires Node 18+ and headless Chromium.
See `remotion/README.md`. Composite rendered alpha overlays back with
`ops.py overlay-video`.

## Gotchas

- The pro pipeline re-encodes several times; use `--basic` for quick drafts and
  the full pipeline for the final render.
- Stabilization is two-pass and crops slightly; disable with `--no-stabilize`
  for tripod/static footage (it is auto-skipped when shake is low).
- Caption timing must match the FINAL cut: if you tighten/re-cut, re-time
  captions (the playbook disables tighten when captions are given).
- Burned-in text uses libass, not drawtext (many static ffmpeg builds lack
  drawtext). Font is picked by NAME via fontconfig — pass `--fontname Arial`
  on systems without DejaVu.
- Beat detection needs music with a clear pulse; `confidence` below ~0.15 means
  you should pass `--bpm` manually or skip `--beat-sync`.
- `morph.py` drops the persona clips' own audio on purpose: the VO is the spine.
  Add SFX afterwards with `ops.py music` or a timeline mix.
- When `morph.py assemble` reports `fit: held last frame`, `qc.py` will flag a
  freeze at that timestamp. That is the two tools agreeing, not a bug — either
  regenerate that persona clip longer or shorten its phrase.
- The tempo estimator can lock an octave low (a 120 BPM click reads as 60). The
  grid still lands on real beats, but pass `--bpm` when you know the tempo.
- LUTs must be `.cube`; they are applied BEFORE the look preset.
- QC exits with status 1 on FAIL, so it can gate a publishing script.
- No built-in transcription: supply SRT / words JSON / text, or use
  `transcribe.py` with a locally installed ASR engine.
