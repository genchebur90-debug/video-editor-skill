---
name: Feature request
about: A new stage, export profile, look, caption style, or backend
title: "feat: "
labels: enhancement
---

## What you want to do

Describe the editorial outcome, not the implementation. "Cuts should land on the snare
rather than the downbeat" is more useful than "add a parameter to rhythm.py".

## Which stage does this belong to?

- [ ] `audio_pro.py` — loudness, dialogue, music
- [ ] `color.py` — tone mapping, grade, LUTs, looks
- [ ] `reframe_smart.py` — subject tracking, safe zones
- [ ] `rhythm.py` — beats, ramps, cuts
- [ ] `captions.py` — caption styles, readability
- [ ] `polish.py` — stabilize, denoise, sharpen, grain
- [ ] `deliver.py` — export profiles
- [ ] `qc.py` — publish checks
- [ ] `localize.py` / `dub.py` / `transcribe.py` — language versions
- [ ] `playbook.py` — one-command flows
- [ ] A new module

## Dependency check

This skill is Python standard library plus ffmpeg, with no pip installs and no API keys.

- [ ] Achievable with stdlib and ffmpeg filters
- [ ] Needs an external tool, which I propose adding as an optional auto-detected backend
      in the style of `transcribe.py` and `dub.py`
- [ ] Needs a paid or cloud service

If the last box is checked, please explain why a free or local alternative will not work.
Paid dependencies are generally out of scope.

## Can it be measured?

Stages here measure before they act: loudness is measured before correction, colour is
read from `signalstats` before grading. Is there a measurable signal your feature can key
off, or does it require a fixed value?

## Reference

Platform spec, broadcast standard, or article that defines the correct behavior, if one
exists.
