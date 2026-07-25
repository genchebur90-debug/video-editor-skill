# Changelog

All notable changes to this skill are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semantic](https://semver.org/spec/v2.0.0.html).

- **Major**: a CLI contract changes, or JSON output shape changes
- **Minor**: a new stage, module, backend, or platform profile
- **Patch**: fixes, filter-compatibility work, documentation

## [Unreleased]

Nothing yet.

## [1.0.0]

First public release. Verified on ffmpeg 7.0.2 with `tests/smoke.sh` at 43 passed,
0 failed.

### Editorial pipeline

- `playbook.py` with three one-command playbooks: `short`, `ad`, `ugc`
- Twelve-stage professional path: dead-air trim, stabilize/denoise/sharpen,
  subject-tracking reframe, HDR tone map and colour, duration-aware push-in, concat with
  optional beat snapping, dialogue cleanup, hook and captions, EQ-carved ducked music,
  two-pass loudness, delivery encode, QC
- `--basic` flag for fast drafts

### Audio

- `audio_pro.py`: two-pass EBU R128 loudness at -14 LUFS for social and -23 for
  broadcast, -1 dBTP true-peak limiting
- Dialogue chain: high-pass, spectral de-noise, gate, de-esser, 3:1 compression,
  presence EQ, with `studio`, `ugc`, `podcast`, and `phone` presets
- Sidechain music ducking with an EQ carve around 2.5 kHz so speech stays intelligible

### Picture

- `reframe_smart.py`: per-column motion and contrast energy locates the subject, with
  smoothed interpolated keyframes and platform safe zones
- `color.py`: HDR to SDR tone mapping in linear light, measurement-driven auto grade,
  `.cube` LUT support, seven looks, BT.709 tagging
- `polish.py`: two-pass vidstab, hqdn3d and nlmeans denoise, luma-only sharpening,
  optional film grain

### Pacing and captions

- `rhythm.py`: pure-stdlib BPM and beat detection, beat-snapped cut plans, speed ramps,
  freeze accents, J/L cuts
- `captions.py`: five styles, eased word pop, smart line breaks on punctuation, emphasis
  word, Netflix-style readability pass with reading-speed and character limits

### Delivery

- `deliver.py`: eight export profiles (`reels`, `tiktok`, `shorts`, `square`, `feed_4x5`,
  `youtube_hd`, `youtube_4k`, `master_prores`), CRF 18 at `slow`, high@4.1, 2 second GOP,
  per-platform VBV cap, AAC 320 kb/s at 48 kHz, faststart
- `qc.py`: pre-publish checks for loudness, true peak, clipping, silence, black frames,
  freezes, aspect, resolution, fps, colour tags, faststart, and duration limits. Exits 1
  on failure so it can gate a publishing script

### Localization, all free and local

- `transcribe.py`: optional ASR via whisperx, faster-whisper, openai-whisper, or
  whisper.cpp, with demucs or filter-based voice isolation
- `localize.py`: offline Argos, local Ollama, or self-hosted LibreTranslate, with glossary
  protection and bilingual caption layout
- `dub.py`: edge-tts, piper, or espeak voice-over, time-fitted to the original cut with
  pitch preservation, original bed retained and ducked

### Tooling

- `doctor.py` environment check with `--smoke` test render
- `probe.py`, `analyze.py`, `ops.py` atomic operations, `render_timeline.py` declarative
  montage, `fetch.py` optional yt-dlp download
- Optional Remotion compositions: `Captions`, `CTAEndCard`, `KineticIntro`
- `tests/smoke.sh` end-to-end suite that synthesizes its own footage

### Design notes

- Text renders through libass rather than `drawtext`, since many static ffmpeg builds ship
  without `drawtext`
- Every command emits compact JSON for agent consumption
- Python standard library only; no pip installs, no API keys, no cloud calls
