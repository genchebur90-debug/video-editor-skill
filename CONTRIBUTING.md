# Contributing to video-editor

The constraint that shapes this whole project: **Python standard library only, plus
`ffmpeg`/`ffprobe` on PATH.** No pip installs, no API keys, no cloud calls. A
contribution that adds a hard dependency will be sent back, however good it is.

Optional integrations are the exception, and they follow a pattern: detect the backend,
degrade gracefully when it is absent, and report what is available. `transcribe.py`,
`localize.py`, and `dub.py` all work this way. Copy that shape.

## Before you open a pull request

Run the environment check and the full self-test:

```bash
python3 video-editor/doctor.py --smoke
bash tests/smoke.sh
```

`smoke.sh` synthesizes its own footage, so it needs no assets and should pass on a bare
machine with ffmpeg installed. If your change touches a render path, the suite must stay
green. If it adds a stage, add a case to the suite.

## House rules

**Every command prints compact JSON.** Not prose, not a progress bar. Output is consumed
by an agent, so it has to be parseable. Errors go to stderr, results to stdout.

**All inputs are local files.** Nothing in this skill fetches a URL on its own, with the
single deliberate exception of `fetch.py`, which exists to be explicit about it.

**Filters must be checked, not assumed.** Static ffmpeg builds vary in what they include.
`doctor.py` probes for what the pipeline needs, and text is rendered through libass
rather than `drawtext` precisely because many builds ship without `drawtext`. If your
change needs a filter, add it to the doctor's probe list.

**Defaults target a professional result, not a fast one.** CRF 18 at `slow`, two-pass
loudness, 2 second GOP. `--basic` exists for drafts. Do not make the draft path the
default to save render time.

**Measure instead of guessing.** The reason this skill sounds and looks correct is that
`audio_pro.py` measures loudness before it corrects it, and `color.py` reads
`signalstats` before it grades. A contribution that hardcodes a gain value or a
saturation bump is doing the thing this project exists to avoid.

## Where things live

| Path | Contains |
|---|---|
| `video-editor/SKILL.md` | The agent-facing contract: commands, flags, recipes, gotchas |
| `video-editor/*.py` | One module per stage, each runnable standalone |
| `video-editor/examples/` | Timeline and word-timing JSON schemas |
| `video-editor/remotion/` | Optional React compositions for richer motion graphics |
| `tests/smoke.sh` | End-to-end suite, synthesizes its own footage |

A new stage gets its own module with a CLI, an entry in the `SKILL.md` script table, and
a case in the smoke test. Keep `SKILL.md` accurate above all else: it is what the agent
reads, so a stale flag there is a real bug, not a documentation nit.

## Good contributions

- New export profiles as platform specs change
- Additional looks and LUT handling in `color.py`
- Better subject tracking in `reframe_smart.py`, which is currently energy-based
- More caption styles in `captions.py`
- Additional free ASR, translation, or TTS backends behind the existing detection pattern
- QC checks that catch a real publish failure

## Reporting a bug

Include your ffmpeg version and the output of `python3 video-editor/doctor.py`. Most
render failures come down to a missing encoder or filter in a static build, and the
doctor output identifies that immediately. Run with `VIDEO_EDITOR_VERBOSE=1` to capture
the exact ffmpeg command that failed.
