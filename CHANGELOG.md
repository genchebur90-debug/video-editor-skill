# Changelog

All notable changes to this skill are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semantic](https://semver.org/spec/v2.0.0.html).

- **Major**: a CLI contract changes, or JSON output shape changes
- **Minor**: a new stage, module, backend, or platform profile
- **Patch**: fixes, filter-compatibility work, documentation

## [Unreleased]

### Added

- `morph.py` — audio-first morph montage for "one continuous voice, many looks"
  reels. Four ops:
  - `plan` derives one slot per phrase from the **real** voice track (silence
    detection finds the gaps between phrases), snaps each boundary to the
    nearest pause and then to the nearest musical beat, and warns when the
    average slot falls outside the 2.5–4.0 s window that reads as a device.
    `snapped_by` reports what moved each cut.
  - `assemble` fits each persona clip to its slot (trim / slow up to
    `--max-stretch` / hold last frame, reported per segment as `fit`), joins
    them through short cut-hiding seams, and lays the continuous VO over the
    top. `--picture` caches the visual chain so audio iterations are seconds.
  - `subs` writes an SRT straight from the plan — timings are already known, so
    no ASR is needed. Supports the `text` (spoken) / `subtitle` (on-screen)
    split for RU audio + EN burned-in subtitles.
  - `seams` lists the seam styles: `hard`, `whip`, `slide`, `flash`, `black`,
    `pixel`, `morph` (0.10–0.30 s each). xfade seams consume their own
    duration, so slots are padded to keep the VO in sync.
- `examples/morph.example.json` — six-persona script template.
- Smoke coverage for the morph path (plan boundaries, SRT cue count, assembled
  duration matching the VO, cached-picture reuse).

### Notes

- `morph.py` deliberately discards the persona clips' own audio: the VO is the
  spine of the format. Layer SFX afterwards.
- A `fit: held last frame` segment will raise a `freeze` WARN in `qc.py`. The
  two tools are agreeing; regenerate that clip longer or shorten the phrase.

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

- `audio_pro.py` — two-pass EBU R128 loudness, dialogue chain, sidechain ducking
- `rhythm.py` — beat detection, beat-snapped cut plans, speed ramps, J/L cuts

### Video

- `reframe_smart.py` — subject-tracking 9:16 reframe
- `color.py` — HDR tone map, auto grade, LUTs, film looks
- `polish.py` — stabilize, denoise, sharpen, grain

### Captions

- `captions.py` — animated word-by-word captions, platform safe zones
- `localize.py` — free offline translation, bilingual captions
- `dub.py` — free local TTS voice-over

### Delivery

- `deliver.py` — platform export profiles, CRF 18, faststart
- `qc.py` — pre-publish QC gate
