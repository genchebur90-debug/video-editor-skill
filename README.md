<div align="center">

# video-editor

**A skill that lets an AI agent edit video like a post-production team, not like a batch script.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-black.svg)](./CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#requirements)
[![Dependencies](https://img.shields.io/badge/pip%20installs-none-brightgreen.svg)](#requirements)
[![Self-test](https://img.shields.io/badge/smoke%20tests-43%20passing-brightgreen.svg)](#verify-the-install)

</div>

---

## What this is

`video-editor` is a **skill** for AI coding agents: a folder of documentation and Python
modules your agent reads, then drives from the command line. Give it raw clips and a
brief, and it returns a finished, platform-ready video.

Ask an agent to edit video without it and you get a batch script: clips glued end to end,
a music bed at a guessed volume, a centre crop that cuts the speaker's head off in 9:16,
and text slapped on top. It renders. It is not edited.

The difference is measurement. This skill measures loudness before correcting it, reads
`signalstats` before grading, and finds the subject before reframing, because the
professional version of each of those steps is a measurement followed by a correction, not
a fixed value someone guessed.

**What it does not do:** it does not generate AI footage, and it cannot watch or interpret
video content. It edits footage you already have.

## What "professional" means concretely

| Amateur default | What this skill does instead |
|---|---|
| Guessed audio gain | Two-pass EBU R128 loudness to -14 LUFS with a -1 dBTP true-peak ceiling |
| Raw phone audio | High-pass, de-noise, gate, de-esser, 3:1 compression, presence EQ |
| Music at a fixed volume | Bed EQ-carved around 2.5 kHz and sidechain-ducked to the voice |
| Centre crop to 9:16 | Per-column motion and contrast energy tracks the subject, pan smoothed |
| Untouched flat or HDR source | Tone mapping in linear light, measured auto grade, `.cube` LUTs |
| Cuts wherever | BPM detection with cuts snapped to the beat |
| Static burned text | Eased word-by-word captions inside platform safe zones |
| Hope it passes | QC on loudness, true peak, clipping, black frames, freezes, colour tags |

Twelve stages run by default. `--basic` renders a fast draft instead.

## Requirements

- `ffmpeg` and `ffprobe` on PATH, with libx264, aac, libass, xfade, zoompan, loudnorm,
  vidstab, and zscale/tonemap
- Python 3.8 or newer

Python **standard library only**. No pip installs, no API keys, no cloud calls. Even the
optional second-language features use free local engines.

## Install

1. Install ffmpeg:

   ```bash
   brew install ffmpeg          # macOS
   sudo apt install ffmpeg      # Linux
   winget install Gyan.FFmpeg   # Windows
   ```

2. Clone into your agent's skills directory:

   ```bash
   # Claude Code, available in every project
   git clone https://github.com/genchebur90-debug/video-editor-skill.git \
     ~/.claude/skills/video-editor-skill

   # Claude Code, this project only
   git clone https://github.com/genchebur90-debug/video-editor-skill.git \
     .claude/skills/video-editor-skill
   ```

   Or download the ZIP from the green **Code** button and copy the `video-editor/` folder
   into your skills directory.

### Verify the install

```bash
python3 video-editor/doctor.py --smoke
```

`doctor.py` probes for every encoder and filter the pipeline needs and reports what is
missing. With `--smoke` it also renders a short captioned test clip, so a pass means the
whole chain works rather than just that ffmpeg exists.

Full end-to-end suite, which synthesizes its own footage and needs no assets:

```bash
bash tests/smoke.sh
```

Expected result: `43 passed, 0 failed`.

## One command, finished video

```bash
# Reels / TikTok / Shorts
python3 video-editor/playbook.py short --clips a.mp4 b.mp4 \
  --hook "WATCH TO THE END" --captions words.json --music bed.mp3 \
  --platform tiktok --qc --out short.mp4

# UGC avatar / talking head
python3 video-editor/playbook.py ugc --clips avatar.mp4 --captions words.json \
  --platform reels --out ugc.mp4

# Commercial ad, cuts locked to the beat
python3 video-editor/playbook.py ad --clips product.mp4 broll.mp4 \
  --music bed.mp3 --look teal_orange --beat-sync --two-pass --out ad.mp4
```

Every stage is also available on its own — see `video-editor/SKILL.md`.

## What makes the output "professional"

| stage | what it does |
|-------|--------------|
| `audio_pro.py` | two-pass EBU R128 loudness (-14 LUFS social / -23 broadcast), -1 dBTP true-peak limiting, dialogue chain (de-noise, gate, de-esser, 3:1 compression, presence EQ), EQ-carved sidechain ducking |
| `reframe_smart.py` | finds the subject with per-column motion/contrast energy and pans smoothly — no more heads cropped out of 9:16 |
| `color.py` | HDR->SDR tone mapping in linear light, measurement-driven auto grade, `.cube` LUTs, film looks, BT.709 tagging |
| `rhythm.py` | BPM/beat detection (pure stdlib), beat-snapped cut plan, speed ramps, freeze accents, J/L cuts |
| `captions.py` | eased word pop, smart line breaks on punctuation, emphasis word, TikTok/Reels/Shorts safe zones |
| `polish.py` | two-pass vidstab stabilization, hqdn3d/nlmeans denoise, luma-only sharpening, optional grain |
| `deliver.py` | CRF 18 `slow`, high@4.1, 2 s GOP, VBV cap per platform, AAC 320k/48k, `+faststart`, ProRes master option |
| `transcribe.py` | optional local ASR (whisperx forced alignment = real per-word timings) with demucs/ffmpeg voice isolation |
| `localize.py` | translate captions with **free** engines only (offline argos, local Ollama LLM, self-hosted LibreTranslate), glossary protection, bilingual subtitles |
| `dub.py` | **free** local TTS voice-over (edge-tts / piper / espeak) time-fitted to the original cut, original bed kept and ducked |
| `qc.py` | loudness, true peak, clipping, silence, black frames, freezes, aspect/res/fps, colour tags, faststart, duration limits |

## Second-language version of any video (no paid APIs)

```bash
python3 video-editor/transcribe.py cut.mp4 --out words.json --backend whisperx --separate demucs
python3 video-editor/localize.py translate --words words.json --from ru --to en --out words_en.json
python3 video-editor/dub.py dub --video cut.mp4 --words words_en.json --lang en \
  --keep-original -18 --out cut_en.mp4
```

Every engine used here is free: WhisperX and demucs run locally, translation
uses offline Argos / your local Ollama model / a self-hosted LibreTranslate,
and dubbing uses edge-tts (no key) or fully offline Piper.

## Layout

```
video-editor/
├── SKILL.md              # the agent-facing contract: commands, flags, recipes
├── playbook.py           # one-command editorial flows: short, ad, ugc
├── audio_pro.py          # loudness, dialogue chain, ducked music
├── reframe_smart.py      # subject-tracking 9:16 reframe
├── color.py              # tone mapping, auto grade, LUTs, looks
├── rhythm.py             # beats, cut plans, speed ramps, J/L cuts
├── captions.py           # animated captions, safe zones, readability
├── polish.py             # stabilize, denoise, sharpen, grain
├── deliver.py            # platform export profiles
├── qc.py                 # pre-publish checks, exits 1 on fail
├── transcribe.py         # optional local ASR
├── localize.py           # free translation backends
├── dub.py                # free local TTS voice-over
├── ops.py                # atomic operations
├── analyze.py            # silences, speech, scenes, loudness
├── render_timeline.py    # declarative montage from JSON
├── probe.py              # media info
├── doctor.py             # environment check
├── fetch.py              # optional yt-dlp download
├── examples/             # timeline and word-timing schemas
└── remotion/             # optional React motion graphics

tests/smoke.sh            # end-to-end self-test, 43 cases
```

## FAQ

**Do I need to install Python packages?** No. Standard library only. You need ffmpeg,
which is not a Python package.

**Does it need an API key or internet?** No. Everything runs locally, including the
optional translation and dubbing. `fetch.py` is the only module that touches the network,
and it exists specifically so that behavior is explicit rather than hidden.

**Can it generate video from a prompt?** No. This edits footage you already have. It also
cannot watch or describe video content.

**Why is it slow?** The full pipeline re-encodes several times and defaults to CRF 18 at
the `slow` preset, because the goal is a final master. Use `--basic` for a quick draft.

**A filter is missing on my machine.** Static ffmpeg builds vary. Run `doctor.py` to see
exactly what is absent. Text rendering already avoids `drawtext` for this reason and uses
libass instead.

**My captions drift out of sync.** Caption timing has to match the final cut. If you
re-cut or tighten after building captions, re-time them. The playbook disables dead-air
trimming automatically when you supply captions, for exactly this reason.

**Beat sync picked the wrong tempo.** Beat detection needs a clear pulse. If the reported
`confidence` is below about 0.15, pass `--bpm` manually or skip `--beat-sync`.

**Can I use it outside an agent?** Yes. Every module is a standalone CLI that prints JSON.
The skill layer is documentation that tells an agent how to combine them.

## Notes

- The pro pipeline re-encodes several times. Use `--basic` for fast drafts.
- Transcription is optional and local: `transcribe.py --list-backends`.
- QC exits with status 1 on FAIL so it can gate a publishing script.
- Stabilization crops slightly and is auto-skipped when shake is low. Disable it with
  `--no-stabilize` for tripod footage.
- LUTs must be `.cube` and are applied before the look preset.

## Contributing

The rule that shapes everything: standard library plus ffmpeg, no pip installs, no API
keys. Optional integrations follow the detect-and-degrade pattern used by `transcribe.py`
and `dub.py`. Run `bash tests/smoke.sh` before opening a pull request. See
[CONTRIBUTING.md](./CONTRIBUTING.md).

## License

MIT. See [LICENSE](./LICENSE).
