<div align="center">

# video-editor

**A skill that lets an AI agent edit video like a post-production team, not like a batch script.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-black.svg)](./CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#requirements)
[![Dependencies](https://img.shields.io/badge/pip%20installs-none-brightgreen.svg)](#requirements)
[![Self-test](https://img.shields.io/badge/smoke%20tests-50%20passing-brightgreen.svg)](#verify-the-install)

</div>

---

## What this is

`video-editor` is a **skill** for AI coding agents: a folder of documentation and Python
modules your agent reads, then drives from the command line. Give it raw clips and a
brief, and it returns a finished, platform-ready video.

Ask an agent to edit video without it and you get a batch script: clips glued end to end,
a music bed at a guessed volume, a centre crop that cuts the speaker's head off in 9:16,
and text slapped on top. It renders. It is not edited.

This skill is the difference between rendering and editing.

## What it does

- **Broadcast audio** — two-pass EBU R128 loudness, dialogue cleanup, sidechain ducking
- **Subject-aware reframing** — tracks the speaker, pans smoothly, respects safe zones
- **Colour management** — HDR tone mapping, measured auto grade, LUTs, film looks
- **Beat-synced pacing** — BPM detection, beat-snapped cuts, speed ramps, J/L cuts
- **Morph montage** — one continuous voice over changing personas, cuts snapped to pauses and beats
- **Animated captions** — word-by-word pop, smart line breaks, platform safe zones
- **Platform delivery** — Reels/TikTok/Shorts/YouTube profiles, QC gate

## Requirements

- `ffmpeg` + `ffprobe` on PATH
- Python 3.8+
- No pip installs, no API keys

## Quick start

```bash
# one command: raw clips -> finished video
python3 playbook.py short --clips a.mp4 b.mp4 --hook "WATCH TO THE END" --platform tiktok --out short.mp4

# morph montage: one voice, many looks
python3 morph.py plan --vo vo.wav --script script.json --music bed.mp3 --out plan.json
python3 morph.py assemble plan.json --out cut.mp4 --music bed.mp3
```

## Scripts

| script | purpose |
|--------|---------|
| `doctor.py` | environment check |
| `probe.py` | media info |
| `analyze.py` | silences, speech, scenes, loudness |
| `ops.py` | atomic ops: trim, concat, reframe, speed, volume, overlays, text, subtitles, transitions |
| `audio_pro.py` | two-pass EBU R128 loudness, dialogue chain, sidechain ducking |
| `reframe_smart.py` | subject-tracking 9:16 reframe |
| `color.py` | HDR tone map, auto grade, LUTs, film looks |
| `rhythm.py` | BPM/beat detection, beat-snapped cut plan, speed ramps, J/L cuts |
| `morph.py` | audio-first morph montage: phrase slots from the real voice track, boundaries snapped to pauses then to the beat, persona clips fitted to their slots, cut-hiding seams, SRT from the plan |
| `captions.py` | animated captions, word pop, safe zones |
| `deliver.py` | platform export profiles, CRF 18, faststart |
| `qc.py` | pre-publish QC gate |
| `render_timeline.py` | declarative montage |
| `playbook.py` | one-command playbooks: short, ad, ugc |
| `transcribe.py` | optional local ASR |
| `localize.py` | free offline translation, bilingual captions |
| `dub.py` | free local TTS voice-over |
| `fetch.py` | optional source download |

## File tree

```
video-editor/
├── SKILL.md               # how to use the skill
├── doctor.py              # environment check
├── probe.py               # media info
├── analyze.py             # silences, speech, scenes
├── ops.py                 # atomic operations
├── audio_pro.py           # loudness, dialogue, ducking
├── reframe_smart.py       # subject-tracking reframe
├── color.py               # colour management
├── rhythm.py              # beats, cut plans, speed ramps, J/L cuts
├── morph.py               # audio-first morph montage (one voice, many looks)
├── polish.py              # stabilize, denoise, sharpen, grain
├── captions.py            # animated captions
├── deliver.py             # platform export
├── qc.py                  # pre-publish QC
├── render_timeline.py     # declarative montage
├── playbook.py            # one-command playbooks
├── transcribe.py          # optional ASR
├── localize.py            # free translation
├── dub.py                 # free TTS voice-over
├── fetch.py               # optional download
├── examples/
│   ├── timeline.example.json
│   ├── words.example.json
│   └── morph.example.json
└── remotion/              # optional motion graphics
```

## Verify the install

```bash
bash tests/smoke.sh
```

50 passed, 0 failed.

## License

MIT
