# Motion-graphics layer (Remotion)

Optional React/Remotion compositions for the `video-editor` skill: animated
captions, a CTA end-card, and a kinetic intro title.

> **Where this runs.** Remotion renders via headless Chromium. It works on your
> machine / Claude Code / CI where Chromium is available. In the Hyperagent
> sandbox it is **not** provisioned by default (npm registry is firewalled and a
> few Chromium system libs are missing), so on-platform the skill produces the
> same effects with `captions.py` (libass) or HyperFrames. Everything here is
> shipped for portability and is **untested inside the sandbox** — run it where
> Chromium works.

## Setup (your environment)

```bash
cd remotion
npm install          # Remotion + React
npm run studio       # optional: live preview at localhost:3000
```

## Render

```bash
# Alpha caption overlay (composite over footage)
node render.mjs Captions caps.webm props/captions.json     # vp8 + yuva420p

# CTA end-card (full frame, normal mp4)
node render.mjs CTAEndCard cta.mp4 props/cta.json

# Kinetic intro (alpha overlay)
node render.mjs KineticIntro intro.mov props/intro.json    # prores 4444
```

`props/*.json` matches each component's props, e.g. captions:

```json
{ "accent": "#00E5FF",
  "words": [ {"text":"Stop","start":0.0,"end":0.45}, {"text":"scrolling","start":0.45,"end":1.0} ] }
```

## Composite back with the FFmpeg engine

```bash
# overlay an alpha caption clip centred over the edited video
python3 ../ops.py overlay-video edit.mp4 caps.webm final.mp4 --position center

# or append the CTA card
python3 ../render_timeline.py timeline.json   # list cta.mp4 as the last clip
```

## Compositions

| id | props | background | render as |
|----|-------|-----------|-----------|
| `Captions` | `words[]`, `accent`, `fontSize?`, `perLine?` | transparent | `.webm`/`.mov` |
| `CTAEndCard` | `text`, `accent`, `sub?` | solid | `.mp4` |
| `KineticIntro` | `text`, `accent` | transparent | `.webm`/`.mov` |
