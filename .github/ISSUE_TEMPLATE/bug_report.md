---
name: Bug report
about: A render fails, produces wrong output, or a command errors
title: "bug: "
labels: bug
---

## What happened

## What you expected

## Command

```bash
# the exact command, with flags
```

## Environment

Paste the full output of the doctor. Most render failures are a missing encoder or filter
in a static ffmpeg build, and this identifies that immediately.

```
python3 video-editor/doctor.py
```

<details>
<summary>doctor output</summary>

```
paste here
```

</details>

## The failing ffmpeg command

Re-run with verbose mode so the exact ffmpeg invocation is echoed to stderr:

```bash
VIDEO_EDITOR_VERBOSE=1 python3 video-editor/... 2> ffmpeg-log.txt
```

<details>
<summary>error output</summary>

```
paste here
```

</details>

## Source media

Output of `python3 video-editor/probe.py your-input.mp4`, which reports duration,
resolution, fps, codecs, and audio layout without needing the file itself.

```
paste here
```

## Does the self-test pass?

```bash
bash tests/smoke.sh
```

- [ ] Passes, so the issue is specific to my media or flags
- [ ] Fails too, so my environment is the problem
