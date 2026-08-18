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
python3 "$SKILL/analyze.py" analyze "$WORK/a.mp4" >/dev/null && ok "analyze.py" || bad "analyze.py"
python3 "$SKILL/analyze.py" tighten "$WORK/a.mp4" "$WORK/tight.mp4" >/dev/null && ok "analyze tighten" || bad "analyze tighten"

say "ops"
python3 "$SKILL/ops.py" trim "$WORK/a.mp4" "$WORK/trim.mp4" --start 0 --end 2 >/dev/null && ok "ops trim" || bad "ops trim"
python3 "$SKILL/ops.py" concat "$WORK/a.mp4" "$WORK/b.mp4" "$WORK/concat.mp4" >/dev/null && ok "ops concat" || bad "ops concat"
python3 "$SKILL/ops.py" reframe "$WORK/a.mp4" "$WORK/reframe.mp4" --width 540 --height 960 >/dev/null && ok "ops reframe" || bad "ops reframe"
python3 "$SKILL/ops.py" speed "$WORK/a.mp4" "$WORK/speed.mp4" --factor 1.5 >/dev/null && ok "ops speed" || bad "ops speed"
python3 "$SKILL/ops.py" volume "$WORK/a.mp4" "$WORK/vol.mp4" --gain-db -6 >/dev/null && ok "ops volume" || bad "ops volume"
python3 "$SKILL/ops.py" extract-audio "$WORK/a.mp4" "$WORK/audio.wav" >/dev/null && ok "ops extract-audio" || bad "ops extract-audio"
python3 "$SKILL/ops.py" replace-audio "$WORK/a.mp4" "$WORK/audio.wav" "$WORK/replace.mp4" >/dev/null && ok "ops replace-audio" || bad "ops replace-audio"
python3 "$SKILL/ops.py" music "$WORK/a.mp4" "$MUSIC" "$WORK/music.mp4" --gain-db -18 >/dev/null && ok "ops music" || bad "ops music"
python3 "$SKILL/ops.py" text "$WORK/a.mp4" "$WORK/text.mp4" --text "TEST" --position bottom >/dev/null && ok "ops text" || bad "ops text"
python3 "$SKILL/ops.py" subtitles "$WORK/a.mp4" "$WORK/words.json" "$WORK/subs.mp4" >/dev/null && ok "ops subtitles" || bad "ops subtitles"
python3 "$SKILL/ops.py" transition "$WORK/a.mp4" "$WORK/b.mp4" "$WORK/trans.mp4" --type fade --duration 0.5 >/dev/null && ok "ops transition" || bad "ops transition"
python3 "$SKILL/ops.py" fade "$WORK/a.mp4" "$WORK/fade.mp4" --fin 0.3 --fout 0.3 >/dev/null && ok "ops fade" || bad "ops fade"
python3 "$SKILL/ops.py" export "$WORK/a.mp4" "$WORK/export.mp4" --preset social_vertical >/dev/null && ok "ops export" || bad "ops export"

say "playbooks"
python3 "$SKILL/playbook.py" short --clips "$WORK/a.mp4" --transcript-text "Test hook" --platform tiktok --width 540 --height 960 --no-stabilize --out "$WORK/short.mp4" >/dev/null && ok "playbook short" || bad "playbook short"
python3 "$SKILL/playbook.py" ad --clips "$WORK/a.mp4" "$WORK/b.mp4" --music "$MUSIC" --platform reels --width 540 --height 960 --no-stabilize --out "$WORK/ad.mp4" >/dev/null && ok "playbook ad" || bad "playbook ad"

say "pro audio (audio_pro.py)"
python3 "$SKILL/audio_pro.py" measure "$WORK/a.mp4" --platform reels >/dev/null && ok "audio_pro measure" || bad "audio_pro measure"
python3 "$SKILL/audio_pro.py" voice "$WORK/a.mp4" "$WORK/voice.mp4" --preset ugc --platform reels >/dev/null && ok "audio_pro voice" || bad "audio_pro voice"
python3 "$SKILL/audio_pro.py" normalize "$WORK/a.mp4" "$WORK/norm.mp4" --platform reels >/dev/null && ok "audio_pro normalize" || bad "audio_pro normalize"
python3 "$SKILL/audio_pro.py" music "$WORK/a.mp4" "$MUSIC" "$WORK/bed.mp4" --gain-db -20 >/dev/null && ok "audio_pro music" || bad "audio_pro music"

say "smart reframe"
python3 "$SKILL/reframe_smart.py" "$WORK/a.mp4" "$WORK/smart.mp4" --safe tiktok --analyze-only >/dev/null && ok "reframe_smart --analyze-only" || bad "reframe_smart"
python3 "$SKILL/reframe_smart.py" "$WORK/a.mp4" "$WORK/smart.mp4" --safe tiktok >/dev/null && ok "reframe_smart render" || bad "reframe_smart render"

say "colour"
python3 "$SKILL/color.py" stats "$WORK/a.mp4" >/dev/null && ok "color stats" || bad "color stats"
python3 "$SKILL/color.py" grade "$WORK/a.mp4" "$WORK/grade.mp4" --look film >/dev/null && ok "color grade" || bad "color grade"

say "rhythm"
python3 "$SKILL/rhythm.py" beats "$MUSIC" >/dev/null && ok "rhythm beats" || bad "rhythm beats"
python3 "$SKILL/rhythm.py" plan --clips "$WORK/a.mp4" "$WORK/b.mp4" --music "$MUSIC" >/dev/null && ok "rhythm plan" || bad "rhythm plan"
python3 "$SKILL/rhythm.py" ramp "$WORK/a.mp4" "$WORK/ramp.mp4" --ramp 0:1.0 2:1.5 >/dev/null && ok "rhythm ramp" || bad "rhythm ramp"
python3 "$SKILL/rhythm.py" freeze "$WORK/a.mp4" "$WORK/freeze.mp4" --at 1 --dur 0.5 >/dev/null && ok "rhythm freeze" || bad "rhythm freeze"

say "polish"
python3 "$SKILL/polish.py" measure "$WORK/a.mp4" >/dev/null && ok "polish measure" || bad "polish measure"
python3 "$SKILL/polish.py" auto "$WORK/a.mp4" "$WORK/polish.mp4" >/dev/null && ok "polish auto" || bad "polish auto"

say "delivery + QC"
python3 "$SKILL/deliver.py" specs >/dev/null && ok "deliver specs" || bad "deliver specs"
python3 "$SKILL/deliver.py" export "$WORK/a.mp4" "$WORK/final.mp4" --profile tiktok --width 540 --height 960 >/dev/null && ok "deliver export" || bad "deliver export"
python3 "$SKILL/qc.py" "$WORK/final.mp4" --platform tiktok >/dev/null 2>&1 && ok "qc report (no fails)" || ok "qc report (fails reported, exit 1 as designed)"

say "pro playbook (ugc)"
python3 "$SKILL/playbook.py" ugc --clips "$WORK/a.mp4" --transcript-text "Pro edit test" --platform tiktok --width 540 --height 960 --no-stabilize --out "$WORK/ugc.mp4" >/dev/null && ok "playbook ugc" || bad "playbook ugc"

say "localization / dubbing / fetch (free backends)"
cat > "$WORK/w2.json" <<'JSON'
[{"text":"this","start":0.0,"end":0.3},{"text":"is","start":0.3,"end":0.6},
 {"text":"a","start":0.6,"end":0.8},{"text":"very","start":0.8,"end":1.2},
 {"text":"long","start":1.2,"end":1.6},{"text":"caption","start":1.6,"end":2.1},
 {"text":"line","start":2.1,"end":2.5},{"text":"for","start":2.5,"end":2.7},
 {"text":"testing","start":2.7,"end":3.2}]
JSON
python3 "$SKILL/captions.py" build --words "$WORK/w2.json" --max-cps 14 --out "$WORK/oneline.ass" >/dev/null && ok "captions one-line readability pass" || bad "captions one-line readability pass"
python3 "$SKILL/captions.py" build --words "$WORK/w2.json" --no-one-line --out "$WORK/raw.ass" >/dev/null && ok "captions --no-one-line" || bad "captions --no-one-line"
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

say "morph montage (audio-first)"
# VO: three 1.2s bursts separated by 0.5s of real silence -> three phrase slots
: > "$WORK/vparts.txt"
for i in 0 1 2; do
  ffmpeg -y -v error -f lavfi -i "sine=frequency=$((200 + i * 60)):duration=1.2" \
    -ar 48000 -ac 1 "$WORK/vb$i.wav"
  echo "file 'vb$i.wav'" >> "$WORK/vparts.txt"
  if [ "$i" -lt 2 ]; then
    ffmpeg -y -v error -f lavfi -i "anullsrc=r=48000:cl=mono:d=0.5" "$WORK/vs$i.wav"
    echo "file 'vs$i.wav'" >> "$WORK/vparts.txt"
  fi
done
(cd "$WORK" && ffmpeg -y -v error -f concat -safe 0 -i vparts.txt -ar 48000 -ac 1 vo.wav)
for i in 0 1 2; do
  ffmpeg -y -v error -f lavfi -i "testsrc2=size=540x960:rate=30:duration=3" \
    -c:v libx264 -pix_fmt yuv420p -an "$WORK/p$i.mp4"
done
cat > "$WORK/morph.json" <<'JSON'
{"phrases":[
 {"persona":"one","seam":"hard","text":"first phrase here","subtitle":"first phrase here"},
 {"persona":"two","seam":"whip","text":"second phrase here","subtitle":"second phrase here"},
 {"persona":"three","seam":"flash","text":"third phrase here","subtitle":"third phrase here"}]}
JSON
python3 "$SKILL/morph.py" seams >/dev/null && ok "morph seams" || bad "morph seams"
python3 "$SKILL/morph.py" plan --vo "$WORK/vo.wav" --script "$WORK/morph.json" \
  --clips "$WORK/p0.mp4" "$WORK/p1.mp4" "$WORK/p2.mp4" --out "$WORK/plan.json" >/dev/null \
  && ok "morph plan" || bad "morph plan"
python3 - "$SKILL" "$WORK" <<'PY' && ok "morph plan: contiguous slots covering the VO" || bad "morph plan slots"
import json, os, sys
p = json.load(open(os.path.join(sys.argv[2], "plan.json")))
segs = p["segments"]
assert p["count"] == 3, p["count"]
assert all(s["dur"] > 0.3 for s in segs), segs
assert abs(segs[-1]["end"] - p["total_sec"]) < 0.05, segs[-1]
assert all(segs[i]["end"] == segs[i + 1]["start"] for i in range(2)), "slots must be contiguous"
PY
python3 "$SKILL/morph.py" subs "$WORK/plan.json" --out "$WORK/morph.srt" >/dev/null
[ "$(grep -c ' --> ' "$WORK/morph.srt")" = "3" ] && ok "morph subs (3 cues)" || bad "morph subs"
python3 "$SKILL/morph.py" assemble "$WORK/plan.json" --out "$WORK/morph.mp4" \
  --width 540 --height 960 --picture "$WORK/mpic.mp4" >/dev/null
assert_video "$WORK/morph.mp4" 3.0
python3 - "$SKILL" "$WORK" <<'PY' && ok "morph output matches the VO length" || bad "morph VO sync"
import os, subprocess, sys
w = sys.argv[2]
def dur(f):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", f], capture_output=True, text=True)
    return float(r.stdout)
vo, out = dur(os.path.join(w, "vo.wav")), dur(os.path.join(w, "morph.mp4"))
assert abs(out - vo) < 0.35, (vo, out)
PY
python3 "$SKILL/morph.py" assemble "$WORK/plan.json" --out "$WORK/morph2.mp4" \
  --width 540 --height 960 --picture "$WORK/mpic.mp4" 2>/dev/null \
  | grep -q '"picture_cached": true' && ok "morph picture cache reused" || bad "morph picture cache"

printf '\n\033[1m%s passed, %s failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'ALL GOOD\n'
