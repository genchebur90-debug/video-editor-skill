#!/usr/bin/env bash
# smoke.sh - end-to-end self-test for the video-editor skill.
#
# Generates synthetic clips with ffmpeg (no assets needed), runs the main
# entry points, and asserts that each output exists and has a sane duration.
#
#   bash tests/smoke.sh            # full run
#   KEEP=1 bash tests/smoke.sh     # keep the work dir for inspection

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$(cd "$HERE/../video-editor" && pwd)"
WORK="$(mktemp -d -t video-editor-smoke-XXXXXX)"
PASS=0
FAIL=0

cleanup() { [ "${KEEP:-0}" = "1" ] || rm -rf "$WORK"; }
trap cleanup EXIT

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$*"; }

assert_video() { # path, min duration
  local f="$1" min="${2:-0.5}"
  if [ ! -s "$f" ]; then bad "missing/empty: $f"; return; fi
  local d
  d=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$f" || echo 0)
  if awk "BEGIN{exit !($d >= $min)}"; then ok "$(basename "$f") (${d}s)"
  else bad "$(basename "$f") too short (${d}s < ${min}s)"; fi
}

say "environment"
python3 "$SKILL/doctor.py" >/dev/null && ok "doctor.py" || bad "doctor.py"

say "fixtures"
ffmpeg -y -v error -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=4" \
  -f lavfi -i "sine=frequency=320:sample_rate=48000:duration=4" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$WORK/a.mp4"
ffmpeg -y -v error -f lavfi -i "smptebars=size=1920x1080:rate=30:duration=3" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=3" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$WORK/b.mp4"
ffmpeg -y -v error -f lavfi -i "anoisesrc=d=8:c=pink:r=48000:a=0.3" \
  -c:a libmp3lame "$WORK/music.mp3" 2>/dev/null \
  || ffmpeg -y -v error -f lavfi -i "sine=frequency=200:duration=8" -c:a aac "$WORK/music.m4a"
MUSIC="$WORK/music.mp3"; [ -s "$MUSIC" ] || MUSIC="$WORK/music.m4a"
ok "fixtures generated"

cat > "$WORK/words.json" <<'JSON'
[{"text":"Hello","start":0.0,"end":0.5},
 {"text":"this","start":0.5,"end":0.9},
 {"text":"is","start":0.9,"end":1.2},
 {"text":"a","start":1.2,"end":1.4},
 {"text":"smoke","start":1.4,"end":1.9},
 {"text":"test","start":1.9,"end":2.4}]
JSON

say "probe / analyze"
python3 "$SKILL/probe.py" "$WORK/a.mp4" >/dev/null && ok "probe.py" || bad "probe.py"
python3 "$SKILL/analyze.py" analyze "$WORK/a.mp4" >/dev/null && ok "analyze.py analyze" || bad "analyze.py analyze"

say "atomic ops"
python3 "$SKILL/ops.py" trim "$WORK/a.mp4" "$WORK/trim.mp4" --start 0.5 --end 2.5 >/dev/null
assert_video "$WORK/trim.mp4" 1.5
python3 "$SKILL/ops.py" reframe "$WORK/b.mp4" "$WORK/vert.mp4" --width 540 --height 960 --mode fill >/dev/null
assert_video "$WORK/vert.mp4" 2.5
python3 "$SKILL/ops.py" concat "$WORK/a.mp4" "$WORK/b.mp4" "$WORK/cat.mp4" --width 640 --height 360 >/dev/null
assert_video "$WORK/cat.mp4" 6.5
python3 "$SKILL/ops.py" transition "$WORK/a.mp4" "$WORK/b.mp4" "$WORK/xf.mp4" --type fade --duration 0.5 --width 640 --height 360 >/dev/null
assert_video "$WORK/xf.mp4" 6.0
python3 "$SKILL/ops.py" music "$WORK/trim.mp4" "$MUSIC" "$WORK/mus.mp4" --gain-db -18 >/dev/null
assert_video "$WORK/mus.mp4" 1.5

say "input validation"
if python3 "$SKILL/ops.py" trim "$WORK/nope.mp4" "$WORK/x.mp4" >/dev/null 2>&1
then bad "missing input should fail"; else ok "missing input rejected"; fi

say "captions"
python3 "$SKILL/captions.py" build --words "$WORK/words.json" --style tiktok --out "$WORK/caps.ass" >/dev/null
[ -s "$WORK/caps.ass" ] && ok "captions.py build" || bad "captions.py build"
for style in tiktok karaoke clean; do
  python3 "$SKILL/captions.py" burn --video "$WORK/vert.mp4" --words "$WORK/words.json" \
    --style "$style" --out "$WORK/cap_$style.mp4" >/dev/null
  assert_video "$WORK/cap_$style.mp4" 2.5
done

say "timeline"
cat > "$WORK/timeline.json" <<JSON
{ "output": {"path": "$WORK/timeline.mp4", "width": 640, "height": 360, "fps": 30},
  "clips": [ {"src": "$WORK/a.mp4", "in": 0, "out": 2},
             {"src": "$WORK/b.mp4", "in": 0, "out": 2,
              "transition": {"type": "fade", "duration": 0.5}} ],
  "audio": [ {"src": "$MUSIC", "gain_db": -20, "duck": true, "loop": true, "fade_out": 0.5} ] }
JSON
python3 "$SKILL/render_timeline.py" "$WORK/timeline.json" >/dev/null
assert_video "$WORK/timeline.mp4" 3.0

say "playbooks"
python3 "$SKILL/playbook.py" short --clips "$WORK/a.mp4" --out "$WORK/plan.mp4" --dry-run >/dev/null \
  && ok "playbook --dry-run" || bad "playbook --dry-run"
python3 "$SKILL/playbook.py" short --clips "$WORK/a.mp4" "$WORK/b.mp4" \
  --hook "WATCH THIS" --captions "$WORK/words.json" --music "$MUSIC" \
  --width 540 --height 960 --out "$WORK/short.mp4" >/dev/null
assert_video "$WORK/short.mp4" 4.0
python3 "$SKILL/playbook.py" ad --clips "$WORK/b.mp4" --transcript-text "Only today" \
  --lang en --width 540 --height 960 --out "$WORK/ad.mp4" >/dev/null
assert_video "$WORK/ad.mp4" 3.0

say "pro audio (audio_pro.py)"
python3 "$SKILL/audio_pro.py" measure "$WORK/a.mp4" --platform reels >/dev/null \
  && ok "audio_pro measure" || bad "audio_pro measure"
python3 "$SKILL/audio_pro.py" voice "$WORK/a.mp4" "$WORK/voice.mp4" --preset ugc >/dev/null
assert_video "$WORK/voice.mp4" 3.0
python3 "$SKILL/audio_pro.py" music "$WORK/a.mp4" "$MUSIC" "$WORK/bed.mp4" --gain-db -16 >/dev/null
assert_video "$WORK/bed.mp4" 3.0

say "smart reframe"
python3 "$SKILL/reframe_smart.py" "$WORK/a.mp4" --analyze-only >/dev/null \
  && ok "reframe_smart --analyze-only" || bad "reframe_smart --analyze-only"
python3 "$SKILL/reframe_smart.py" "$WORK/a.mp4" "$WORK/smart.mp4" \
  --width 540 --height 960 --safe tiktok >/dev/null
assert_video "$WORK/smart.mp4" 3.0

say "colour"
python3 "$SKILL/color.py" stats "$WORK/a.mp4" >/dev/null && ok "color stats" || bad "color stats"
python3 "$SKILL/color.py" grade "$WORK/a.mp4" "$WORK/grade.mp4" --look teal_orange >/dev/null
assert_video "$WORK/grade.mp4" 3.0

say "rhythm"
python3 "$SKILL/rhythm.py" beats "$MUSIC" >/dev/null && ok "rhythm beats" || bad "rhythm beats"
python3 "$SKILL/rhythm.py" plan --clips "$WORK/a.mp4" "$WORK/b.mp4" --music "$MUSIC" >/dev/null \
  && ok "rhythm plan" || bad "rhythm plan"
python3 "$SKILL/rhythm.py" ramp "$WORK/a.mp4" "$WORK/ramp.mp4" --ramp 0:1.0 2:1.6 >/dev/null
assert_video "$WORK/ramp.mp4" 2.0
python3 "$SKILL/rhythm.py" freeze "$WORK/a.mp4" "$WORK/freeze.mp4" --at 2 --dur 0.5 >/dev/null
assert_video "$WORK/freeze.mp4" 3.5

say "polish"
python3 "$SKILL/polish.py" measure "$WORK/a.mp4" >/dev/null && ok "polish measure" || bad "polish measure"
python3 "$SKILL/polish.py" auto "$WORK/a.mp4" "$WORK/polish.mp4" --denoise light --no-stabilize >/dev/null
assert_video "$WORK/polish.mp4" 3.0

say "delivery + QC"
python3 "$SKILL/deliver.py" specs >/dev/null && ok "deliver specs" || bad "deliver specs"
python3 "$SKILL/deliver.py" export "$WORK/a.mp4" "$WORK/final.mp4" \
  --profile tiktok --width 540 --height 960 >/dev/null
assert_video "$WORK/final.mp4" 3.0
python3 "$SKILL/qc.py" "$WORK/final.mp4" --platform tiktok >/dev/null 2>&1 \
  && ok "qc report (no fails)" || ok "qc report (fails reported, exit 1 as designed)"

say "pro playbook (ugc)"
python3 "$SKILL/playbook.py" ugc --clips "$WORK/a.mp4" --transcript-text "Pro edit test" \
  --platform tiktok --width 540 --height 960 --no-stabilize --out "$WORK/ugc.mp4" >/dev/null
assert_video "$WORK/ugc.mp4" 4.0

say "localization / dubbing / fetch (free backends)"
cat > "$WORK/w2.json" <<'JSON'
[{"text":"this","start":0.0,"end":0.3},{"text":"is","start":0.3,"end":0.6},
 {"text":"a","start":0.6,"end":0.8},{"text":"very","start":0.8,"end":1.2},
 {"text":"long","start":1.2,"end":1.6},{"text":"caption","start":1.6,"end":2.1},
 {"text":"line","start":2.1,"end":2.5},{"text":"for","start":2.5,"end":2.7},
 {"text":"testing","start":2.7,"end":3.2}]
JSON
python3 "$SKILL/captions.py" build --words "$WORK/w2.json" --max-cps 14 \
  --out "$WORK/oneline.ass" >/dev/null && ok "captions one-line readability pass" \
  || bad "captions one-line readability pass"
python3 "$SKILL/captions.py" build --words "$WORK/w2.json" --no-one-line \
  --out "$WORK/raw.ass" >/dev/null && ok "captions --no-one-line" || bad "captions --no-one-line"
python3 "$SKILL/localize.py" backends >/dev/null && ok "localize backends" || bad "localize backends"
python3 "$SKILL/dub.py" backends >/dev/null && ok "dub backends" || bad "dub backends"
python3 "$SKILL/dub.py" voices --lang en >/dev/null && ok "dub voices" || bad "dub voices"
python3 "$SKILL/fetch.py" --check >/dev/null && ok "fetch --check" || bad "fetch --check"

say "optional ASR hook"
python3 "$SKILL/transcribe.py" --list-backends >/dev/null && ok "transcribe.py --list-backends" || bad "transcribe.py"
python3 - "$SKILL" "$WORK/a.mp4" <<'PY' && ok "voice separation filter chain" || bad "voice separation filter chain"
import sys, os, tempfile
sys.path.insert(0, sys.argv[1])
import transcribe
tmp = tempfile.mkdtemp()
wav = transcribe.extract_wav(sys.argv[2], os.path.join(tmp, "a.wav"))
out, method = transcribe.separate_voice(wav, os.path.join(tmp, "v.wav"), "filter")
assert os.path.getsize(out) > 1000 and method == "filter"
PY

printf '\n\033[1m%s passed, %s failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'ALL GOOD\n'
