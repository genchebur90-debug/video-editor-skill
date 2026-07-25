#!/usr/bin/env python3
"""captions.py - animated short-form / ad captions rendered via libass.

Turns a transcript (word- or segment-timed) or plain text into styled ASS
captions and (optionally) burns them onto a video. This build of FFmpeg has no
drawtext, so everything goes through libass, which is what real caption tools
use anyway (per-word highlight, karaoke, transforms).

Pro upgrades over a basic burner:
  - motion: every line fades in and each active word pops with an eased
    scale transform (\\t), instead of text snapping on and off
  - smart grouping: lines break on punctuation, pauses and character width,
    never mid-phrase, so the eye reads whole thoughts
  - emphasis: the most meaningful word in a line stays visually weighted
  - platform safe zones: captions are kept clear of the TikTok / Reels /
    Shorts UI so nothing important sits under a button
  - length-weighted timing: long words get more screen time than short ones

Styles:
  tiktok   - one line at a time, ACTIVE word highlighted + popped.
  pop      - tiktok highlighting plus a bouncier per-line entrance.
  karaoke  - \\k timing: the line fills with the accent colour as it is spoken.
  box      - opaque box behind the text (maximum legibility on busy footage).
  clean    - plain lines, current line only (subtle, good for ads/promos).

Inputs (pick one):
  --words words.json   [{"text","start","end"}...]  word- OR segment-level
  --srt   subs.srt     standard SRT (expanded to word timings)
  --text  "..."        + --duration SECONDS (auto-timed, no transcript needed)

Examples:
  python3 captions.py burn --video v.mp4 --words w.json --style tiktok \\
          --accent "#00E5FF" --safe tiktok --out out.mp4
  python3 captions.py build --srt subs.srt --style clean --out caps.ass
"""
import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402

WHITE = "&H00FFFFFF"          # style colour (with alpha byte)
WHITE_INLINE = "&HFFFFFF&"    # inline \1c override

# Fraction of frame height occupied by platform UI at the bottom of the screen.
SAFE_ZONES = {
    "tiktok": 0.22,
    "reels": 0.24,
    "shorts": 0.18,
    "youtube": 0.12,
    "none": 0.06,
}

# Words that should never be picked as the emphasised word in a line.
_STOPWORDS = set("""a an the and or but if of to in on for with at by from as is
are was were be been am do does did this that these those it its i you he she
we they me him her them my your our their not no so than then too very can
will just should now""".split()) | set(
    "и в во не на что он она они мы вы то а но да как из за по для от до бы же ли "
    "вот так вся все всё это этот эта был была было были есть нет ещё еще уже "
    "там тут ну о об у ты я к с со при про над под".split())


def _inline_color(spec):
    name, _ = ops._parse_color(spec)
    if name.startswith("#") and len(name) >= 7:
        r, g, b = int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16)
    else:
        r, g, b = ops._NAMED_RGB.get(name.lower(), (0, 229, 255))
    return f"&H{b:02X}{g:02X}{r:02X}&"


def parse_srt(path):
    segs = []
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        tc = next((ln for ln in lines if "-->" in ln), None)
        if not tc:
            continue
        body = " ".join(ln for ln in lines if ln is not tc and not ln.strip().isdigit())
        m = re.findall(r"(\d+):(\d+):(\d+)[,.](\d+)", tc)
        if len(m) < 2:
            continue

        def _s(t):
            h, mn, se, ms = map(int, t)
            return h * 3600 + mn * 60 + se + ms / 1000.0

        segs.append({"text": body, "start": _s(m[0]), "end": _s(m[1])})
    return segs


def words_from_segments(segs):
    """Expand segment timings to word timings, weighted by word length.

    Long words genuinely take longer to say, so length weighting tracks real
    speech much better than dividing the segment into equal slices.
    """
    words = []
    for seg in segs:
        toks = str(seg["text"]).split()
        if not toks:
            continue
        dur = max(0.2, float(seg["end"]) - float(seg["start"]))
        total = sum(len(t) for t in toks) or len(toks)
        t = float(seg["start"])
        for tok in toks:
            share = dur * (len(tok) / total)
            words.append({"text": tok, "start": t, "end": t + share})
            t += share
    return words


def words_from_text(text, duration):
    toks = text.split()
    if not toks:
        return []
    total = sum(len(t) for t in toks) or len(toks)
    words, t = [], 0.0
    for tok in toks:
        share = float(duration) * (len(tok) / total)
        words.append({"text": tok, "start": t, "end": t + share})
        t += share
    return words


def load_words(args):
    if args.words:
        data = json.load(open(args.words, encoding="utf-8"))
        if any(" " in str(d.get("text", "")) for d in data):
            return words_from_segments(data)
        return [{"text": d["text"], "start": float(d["start"]), "end": float(d["end"])}
                for d in data]
    if args.srt:
        return words_from_segments(parse_srt(args.srt))
    if args.text:
        if args.duration is None:
            raise SystemExit("--text requires --duration")
        return words_from_text(args.text, args.duration)
    raise SystemExit("provide --words, --srt, or --text")


def group_words(words, per_line=3, max_chars=24, max_gap=0.7):
    """Break the word stream into readable lines.

    Real caption editors do not chop every N words: they break on punctuation,
    on pauses, and before a line gets too wide to read in one glance.
    """
    lines, cur, chars = [], [], 0
    for i, w in enumerate(words):
        text = str(w["text"])
        cur.append(w)
        chars += len(text) + 1
        ends_phrase = bool(re.search(r"[.!?\u2026,;:\u2014]$", text))
        gap = (float(words[i + 1]["start"]) - float(w["end"])) if i + 1 < len(words) else 0.0
        full = len(cur) >= per_line or chars >= max_chars
        if full or (ends_phrase and len(cur) >= max(2, per_line - 1)) or gap >= max_gap:
            lines.append(cur)
            cur, chars = [], 0
    if cur:
        lines.append(cur)
    return lines


def enforce_readability(lines, max_chars=24, max_cps=20.0, min_dur=0.7):
    """Netflix-style readability pass over grouped caption lines.

    Broadcast subtitling rules that VideoLingo also follows:
      * one line on screen at a time - never a two-line wall of text;
      * a hard character limit per line;
      * a reading-speed limit (characters per second);
      * a minimum on-screen duration so short words do not flash.
    Lines that break the char/CPS limits are split at the best word boundary.
    """
    def chars(ln):
        return len(" ".join(str(x["text"]) for x in ln))

    def cps(ln):
        dur = max(0.001, float(ln[-1]["end"]) - float(ln[0]["start"]))
        return chars(ln) / dur

    out = []
    queue = list(lines)
    guard = 0
    while queue and guard < 10000:
        guard += 1
        ln = queue.pop(0)
        if len(ln) > 1 and (chars(ln) > max_chars or cps(ln) > max_cps):
            # split as close to the middle (by characters) as possible
            total = chars(ln)
            acc, cut = 0, 1
            for i, x in enumerate(ln[:-1]):
                acc += len(str(x["text"])) + 1
                cut = i + 1
                if acc >= total / 2.0:
                    break
            queue.insert(0, ln[cut:])
            queue.insert(0, ln[:cut])
            continue
        out.append(ln)

    # stretch lines that are on screen too briefly, without overlapping
    for i, ln in enumerate(out):
        span = float(ln[-1]["end"]) - float(ln[0]["start"])
        if span < min_dur:
            limit = float(out[i + 1][0]["start"]) if i + 1 < len(out) else None
            want = float(ln[0]["start"]) + min_dur
            if limit is not None:
                want = min(want, limit)
            if want > float(ln[-1]["end"]):
                ln[-1] = dict(ln[-1], end=round(want, 3))
    return out


def _emphasis_index(line):
    """Pick the word carrying the meaning (longest non-stopword, digits win)."""
    best, best_score = -1, 0
    for i, w in enumerate(line):
        t = re.sub(r"[^\w']", "", str(w["text"])).lower()
        if not t or t in _STOPWORDS:
            continue
        score = len(t) + (3 if re.search(r"\d", t) else 0)
        if score > best_score:
            best, best_score = i, score
    return best


def _header(w, h, fontsize, primary, secondary, outline_c, back, align, mv,
            border_style=1, outline=6, shadow=2, bold=1,
            fontname="DejaVu Sans"):
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{fontname},{fontsize},{primary},{secondary},{outline_c},"
        f"{back},{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},"
        f"{align},100,100,{mv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _entrance(style, animate):
    """Per-line entrance animation tags."""
    if not animate:
        return ""
    if style == "pop":
        # overshoot then settle - the eased bounce short-form editors use
        return ("{\\fad(50,60)\\fscx78\\fscy78"
                "\\t(0,90,0.6,\\fscx106\\fscy106)"
                "\\t(90,170,1.4,\\fscx100\\fscy100)}")
    if style == "box":
        return "{\\fad(70,70)}"
    return "{\\fad(60,50)}"


def _word_pop(animate):
    """Eased scale pop applied to the word currently being spoken."""
    return "\\fscx112\\fscy112\\t(0,110,0.7,\\fscx100\\fscy100)" if animate else ""


def build_ass(words, w=1080, h=1920, style="tiktok", accent="#00E5FF",
              per_line=3, fontsize=None, position="center", safe_bottom=None,
              fontname="DejaVu Sans", safe="reels", animate=True,
              emphasis=True, max_chars=24, uppercase=False, max_cps=20.0,
              one_line=True):
    fontsize = fontsize or int(h * 0.052)
    acc_inline = _inline_color(accent)
    acc_full = ops._ass_color(accent)
    align = {"center": 5, "bottom": 2, "top": 8}.get(position, 5)
    if safe_bottom is None:
        safe_bottom = SAFE_ZONES.get(safe, SAFE_ZONES["reels"])
    mv = int(h * safe_bottom) if position == "bottom" else int(h * 0.05)
    outline_c = ops._ass_color("#101010")
    back = ops._ass_color("black@0.55" if style == "box" else "black@0.0")
    reset = f"{{\\1c{WHITE_INLINE}\\b0\\fscx100\\fscy100}}"

    if uppercase:
        words = [dict(x, text=str(x["text"]).upper()) for x in words]
    lines = group_words(words, per_line=per_line, max_chars=max_chars)
    if one_line:
        lines = enforce_readability(lines, max_chars=max_chars, max_cps=max_cps)
    lead = _entrance(style, animate)
    ev = []

    if style == "clean":
        for ln in lines:
            ev.append((ln[0]["start"], ln[-1]["end"],
                       lead + " ".join(x["text"] for x in ln)))
        header = _header(w, h, fontsize, WHITE, WHITE, outline_c, back, align, mv,
                         outline=max(3, int(fontsize * 0.06)), fontname=fontname)
    elif style == "karaoke":
        for ln in lines:
            parts = []
            for x in ln:
                cs = max(1, int(round((x["end"] - x["start"]) * 100)))
                parts.append(f"{{\\k{cs}}}{x['text']}")
            ev.append((ln[0]["start"], ln[-1]["end"], lead + " ".join(parts)))
        # sung => PrimaryColour (accent), unsung => SecondaryColour (white)
        header = _header(w, h, fontsize, acc_full, WHITE, outline_c, back, align, mv,
                         outline=max(4, int(fontsize * 0.1)), fontname=fontname)
    else:  # tiktok / pop / box: per-word highlight with an eased scale pop
        pop = _word_pop(animate)
        for ln in lines:
            emph = _emphasis_index(ln) if emphasis else -1
            for wi, x in enumerate(ln):
                start = x["start"]
                end = ln[wi + 1]["start"] if wi + 1 < len(ln) else x["end"]
                parts = []
                for j, y in enumerate(ln):
                    if j == wi:
                        parts.append(f"{{\\1c{acc_inline}\\b1{pop}}}{y['text']}{reset}")
                    elif j == emph:
                        parts.append(f"{{\\b1\\fscx104\\fscy104}}{y['text']}{reset}")
                    else:
                        parts.append(y["text"])
                prefix = lead if wi == 0 else ""
                ev.append((start, end, prefix + " ".join(parts)))
        header = _header(
            w, h, fontsize, WHITE, WHITE, outline_c, back, align, mv,
            border_style=3 if style == "box" else 1,
            outline=max(4, int(fontsize * (0.06 if style == "box" else 0.12))),
            shadow=3, fontname=fontname)

    body = "".join(
        f"Dialogue: 0,{ops._ass_time(s)},{ops._ass_time(e)},Cap,,0,0,0,,{t}\n"
        for s, e, t in ev)
    f = tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False, encoding="utf-8")
    f.write(header + body)
    f.close()
    return f.name


def build_parser():
    p = argparse.ArgumentParser(description="Animated captions via libass")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "burn"):
        s = sub.add_parser(name)
        s.add_argument("--words")
        s.add_argument("--srt")
        s.add_argument("--text")
        s.add_argument("--duration", type=float)
        s.add_argument("--style", default="tiktok",
                       choices=["tiktok", "pop", "karaoke", "box", "clean"])
        s.add_argument("--accent", default="#00E5FF")
        s.add_argument("--per-line", type=int, default=3)
        s.add_argument("--max-chars", type=int, default=24,
                       help="max characters per caption line")
        s.add_argument("--fontsize", type=int)
        s.add_argument("--position", default="center", choices=["center", "bottom", "top"])
        s.add_argument("--safe", default="reels", choices=sorted(SAFE_ZONES),
                       help="platform UI safe zone used for the bottom margin")
        s.add_argument("--safe-bottom", type=float,
                       help="override the safe-zone fraction directly")
        s.add_argument("--no-animate", dest="animate", action="store_false")
        s.add_argument("--no-emphasis", dest="emphasis", action="store_false")
        s.add_argument("--uppercase", action="store_true")
        s.add_argument("--max-cps", type=float, default=20.0,
                       help="max reading speed in characters per second")
        s.add_argument("--no-one-line", dest="one_line", action="store_false",
                       help="disable the single-line readability pass")
        s.add_argument("--fontname", default="DejaVu Sans")
        s.add_argument("--width", type=int, default=1080)
        s.add_argument("--height", type=int, default=1920)
        s.add_argument("--out", required=True)
        if name == "burn":
            s.add_argument("--video", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "burn":
        w, h = ops._dims(args.video)
        w, h = w or args.width, h or args.height
    else:
        w, h = args.width, args.height
    words = load_words(args)
    ass_path = build_ass(words, w=w, h=h, style=args.style, accent=args.accent,
                         per_line=args.per_line, fontsize=args.fontsize,
                         position=args.position, safe_bottom=args.safe_bottom,
                         fontname=args.fontname, safe=args.safe,
                         animate=args.animate, emphasis=args.emphasis,
                         max_chars=args.max_chars, uppercase=args.uppercase,
                         max_cps=args.max_cps, one_line=args.one_line)
    if args.cmd == "build":
        os.replace(ass_path, args.out)
        print(json.dumps({"ok": True, "ass": args.out, "events": len(words),
                          "lines": len(enforce_readability(
                              group_words(words, args.per_line, args.max_chars),
                              args.max_chars, args.max_cps)
                              if args.one_line else
                              group_words(words, args.per_line, args.max_chars))}))
    else:
        ops.burn_subtitles(args.video, ass_path, args.out)
        os.unlink(ass_path)
        print(json.dumps({"ok": True, "output": args.out,
                          "duration_sec": round(ops._dur(args.out), 3)}))


if __name__ == "__main__":
    main()
