"""Check that all parts of a work line up bar by bar (no part is shifted by one or more bars).

Two layers of checks, both only read the scores:

  Layer 1, structure (problems):
    - each movement has the same number of bars in all parts
    - the time signature of each bar is the same in all parts
  Layer 1, structure (warnings):
    - key changes, final/double barlines, repeats and volta endings are at the same bars
    - a bar is short (fewer beats than the time signature) in some parts only
  Layer 2, markings (suspects):
    Dynamics (p, f, sf), hairpins, words such as pizz./arco/cresc. and fermatas are usually in the same bar
    in all parts. When the markings of one part fit the other parts better 1 or 2 bars away,
    the notes of that part are probably in the wrong bars ("?" lines).
    The test cannot see a shift where a part has few markings. The report lists these bars as "not checked".
  A suspect is not a proof. Look at each suspect, and check the "not checked" bars in another way
  (docs/AGENT_SCORE_GUIDE.md, "Check the alignment").

  SP_DATA_DIR=~/my-data uv run python check_alignment.py <work-slug>            # one imported work
  SP_DATA_DIR=~/my-data uv run python check_alignment.py <work-slug> --json
  SP_DATA_DIR=~/my-data uv run python check_alignment.py <work-slug> --bars 1:50-65
      # all parts of movement 1, bars 50-65, as compact text (for a person or an agent)
  SP_DATA_DIR=~/my-data uv run python check_alignment.py --list                  # work slugs

For a ZIP or score files that are not imported yet, use check_import.py: it runs these checks too.

Bar numbers: "1:57" = movement 1, bar 57, counted as in the printed score (a pickup bar is bar 0).
"app bar" = the bar number in the app (all movements counted together, from 1).

Exit code: 0 = no problems; 1 = layer 1 problems; 2 = bad arguments. Warnings and suspects do not change it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from fractions import Fraction

from lxml import etree

import works
from xmlsafe import PARSER

STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
LAGS = (-2, -1, 1, 2)    # shifts that layer 2 tries, in bars
MARK_TOLERANCE = Fraction(1, 2)  # a marking matches when it is this close (quarter notes) to the same marking
MIN_SCORE = 3            # a suspect needs this many more markings that fit shifted than fit in place
CHECK_WINDOW = 16        # coverage: a bar is checked when a window of this many bars around it ...
CHECK_MIN_MARKS = 3      # ... has this many markings that the other parts share
UNCHECKED_MIN = 8        # report unchecked ranges with at least this many bars with notes


@dataclass
class Note:
    start: Fraction      # quarter notes from the start of the bar
    end: Fraction
    pc: int              # pitch class 0-11
    name: str            # for example "C#5"
    onset: bool          # False for the second note of a tie


@dataclass
class Bar:
    nominal: Fraction    # bar length from the time signature, in quarter notes
    filled: Fraction     # end of the last note or rest
    time: str            # for example "3/4"
    key_change: bool
    lines: tuple         # barline styles, repeats and endings in this bar
    notes: list[Note] = field(default_factory=list)
    marks: list[tuple[str, Fraction]] = field(default_factory=list)  # (dynamics, hairpin, pizz., fermata...; time)
    movement_start: bool = False


def _pitch(note) -> tuple[int, str] | None:
    p = note.find("pitch")
    if p is None:
        return None
    step, octave = p.findtext("step"), p.findtext("octave")
    try:
        alter = round(float(p.findtext("alter") or 0))
    except ValueError:
        alter = 0
    if step not in STEP:
        return None
    acc = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}.get(alter, "")
    return (STEP[step] + alter) % 12, f"{step}{acc}{octave or ''}"


DYNAMIC_ALIASES = {"sfz": "sf", "fz": "sf", "rfz": "sf", "rf": "sf", "sffz": "sf"}
WORDS = ("pizz", "arco", "cresc", "dim", "dolce", "espr", "legg", "tremolo", "sord", "solo", "tutti", "div", "unis")


def _direction_marks(direction) -> list[str]:
    """Marks that the parts of one orchestra or ensemble usually share in the same bar."""
    out = []
    for dt in direction.findall("direction-type"):
        for ch in dt:
            if ch.tag == "dynamics":
                for d in ch:
                    if d.tag != "other-dynamics":
                        out.append("dyn-" + DYNAMIC_ALIASES.get(d.tag, d.tag))
            elif ch.tag == "wedge" and ch.get("type") in ("crescendo", "diminuendo"):
                out.append("hairpin-" + ch.get("type"))
            elif ch.tag == "words" and ch.text:
                w = ch.text.strip().lower()
                for key in WORDS:
                    if w.startswith(key):
                        out.append("word-" + key)
                        break
    return out


def read_part(part) -> list[Bar]:
    """All bars of one <part>, with notes in quarter-note time."""
    div, beats, beat_type, time = 1, 4, 4, "4/4"
    bars = []
    for m in part.findall("measure"):
        key_change = False
        for a in m.findall("attributes"):
            d = a.findtext("divisions")
            if d and d.strip().isdigit() and int(d) > 0:
                div = int(d)
            t = a.find("time")
            if t is not None:
                try:
                    beats, beat_type = int(t.findtext("beats")), int(t.findtext("beat-type"))
                    time = f"{beats}/{beat_type}"
                except (TypeError, ValueError):
                    pass  # for example "3+2": keep the previous time signature (as prepare.py does)
            if a.find("key") is not None and bars:
                key_change = True
        lines = []
        for b in m.findall("barline"):
            style = b.findtext("bar-style")
            if style in ("light-heavy", "light-light", "heavy-light", "heavy-heavy"):
                lines.append(style)
            r = b.find("repeat")
            if r is not None:
                lines.append("repeat-" + (r.get("direction") or ""))
            e = b.find("ending")
            if e is not None and e.get("type") == "start":
                lines.append("ending-" + (e.get("number") or ""))
        bar = Bar(nominal=Fraction(4 * beats, beat_type), filled=Fraction(0), time=time,
                  key_change=key_change, lines=tuple(sorted(lines)),
                  movement_start=(m.get("id") or "").startswith("movement-"))
        pos = last = Fraction(0)
        for el in m:
            if el.tag == "backup":
                pos -= Fraction(int(el.findtext("duration") or 0), div)
            elif el.tag == "forward":
                pos += Fraction(int(el.findtext("duration") or 0), div)
            elif el.tag == "direction":
                off = Fraction(int(el.findtext("offset") or 0), div)
                for tok in _direction_marks(el):
                    bar.marks.append((tok, pos + off))
            elif el.tag == "note":
                if el.find("grace") is not None or el.find("cue") is not None:
                    continue
                if el.find("notations/fermata") is not None:
                    bar.marks.append(("fermata", last if el.find("chord") is not None else pos))
                dur = Fraction(int(el.findtext("duration") or 0), div)
                if el.find("chord") is not None:
                    start = last
                else:
                    start = last = pos
                    pos += dur
                bar.filled = max(bar.filled, start + dur)
                pitch = _pitch(el)
                if pitch is not None:
                    tie_stop = any(t.get("type") == "stop" for t in el.findall("tie"))
                    bar.notes.append(Note(start, start + dur, pitch[0], pitch[1], not tie_stop))
        bars.append(bar)
    if bars:
        bars[0].movement_start = True
    return bars


def movements(bars: list[Bar]) -> list[tuple[int, int]]:
    """(first, end) bar index of each movement; end is exclusive."""
    starts = [i for i, b in enumerate(bars) if b.movement_start] or [0]
    return [(s, e) for s, e in zip(starts, starts[1:] + [len(bars)])]


def load_work(work_slug: str) -> list[tuple[str, list[Bar]]]:
    parts = []
    for pdir, meta in works.pieces_of(work_slug):
        root = etree.parse(str(pdir / "score.musicxml"), PARSER).getroot()
        part = root.find("part")
        if part is not None:
            parts.append((str(meta.get("part") or pdir.name), read_part(part)))
    return parts


class Numbering:
    """Bar labels "movement:bar" as in the printed score, from the first part."""

    def __init__(self, bars: list[Bar], all_parts: list[list[Bar]]):
        self.mvts = movements(bars)
        self.pickup = []
        for s, e in self.mvts:
            # a pickup: the first bar is short in every part
            self.pickup.append(all(len(p) > s and p[s].filled < p[s].nominal for p in all_parts))

    def label(self, i: int) -> str:
        for k, (s, e) in enumerate(self.mvts):
            if s <= i < e:
                return f"{k + 1}:{i - s + (0 if self.pickup[k] else 1)}"
        return f"?:{i + 1}"

    def index(self, mvt: int, bar: int) -> int:
        s, e = self.mvts[mvt - 1]
        return s + bar - (0 if self.pickup[mvt - 1] else 1)


# ---------- layer 1 ----------
# Each problem and warning is a dict: "kind", "text" (English, for the CLI) and the data
# (the app writes its own text from the data). Bar positions: "first" = printed label, "app_bar" = app bar (from 1).

def check_structure(parts: list[tuple[str, list[Bar]]]) -> tuple[list[dict], list[dict]]:
    problems, warnings = [], []
    names = [n for n, _ in parts]
    counts = {n: [e - s for s, e in movements(b)] for n, b in parts}
    if len({len(c) for c in counts.values()}) > 1:
        per = {n: len(c) for n, c in counts.items()}
        problems.append({"kind": "movements", "counts": per,
                         "text": "different movement counts: " + ", ".join(f"{n} {c}" for n, c in per.items())})
        return problems, warnings
    for k in range(len(counts[names[0]])):
        per = {n: counts[n][k] for n in names}
        if len(set(per.values())) > 1:
            problems.append({"kind": "bars", "movement": k + 1, "counts": per,
                             "text": f"movement {k + 1}: different bar counts: " + ", ".join(f"{n} {c}" for n, c in per.items())})
    if problems:
        return problems, warnings
    bars0 = parts[0][1]
    num = Numbering(bars0, [b for _, b in parts])

    def diff(kind: str, what: str, part: str, bad: list[int], got: str, want: str, **extra) -> dict:
        i = bad[0]
        return {"kind": kind, **extra, "part": part, "against": names[0], "count": len(bad),
                "first": num.label(i), "app_bar": i + 1, "got": got, "want": want,
                "text": f"{part} vs {names[0]}: {what} differs at {len(bad)} bar(s), first at bar {num.label(i)}"
                        f" (app bar {i + 1}): {got} vs {want}"}

    for n, bars in parts[1:]:
        bad = [i for i in range(len(bars0)) if bars[i].time != bars0[i].time]
        if bad:
            problems.append(diff("time", "time signature", n, bad, bars[bad[0]].time, bars0[bad[0]].time))
        bad = [i for i in range(len(bars0)) if bars[i].key_change != bars0[i].key_change]
        if bad:
            yes = lambda b: "key change" if b.key_change else "-"  # noqa: E731
            warnings.append(diff("key", "key change", n, bad, yes(bars[bad[0]]), yes(bars0[bad[0]])))
    # Barline marks: compare each kind only between parts that use it in the movement.
    # Exports often put volta brackets (endings) in the top part only.
    for kind in ("light", "heavy", "repeat", "ending"):
        def of(b: Bar) -> list[str]:
            return [x for x in b.lines if x.startswith(kind + "-")]
        used0 = [any(of(b) for b in bars0[s:e]) for s, e in num.mvts]
        for n, bars in parts[1:]:
            used = [any(of(b) for b in bars[s:e]) for s, e in num.mvts]
            bad = [i for k, (s, e) in enumerate(num.mvts) if used[k] and used0[k] for i in range(s, e)
                   if of(bars[i]) != of(bars0[i])]
            if bad:
                warnings.append(diff("marks", f"{kind} marks", n, bad, " ".join(of(bars[bad[0]])) or "-",
                                     " ".join(of(bars0[bad[0]])) or "-", mark=kind))
    # Short bars: compare only parts that have notes in the bar (a pickup in one part, whole-bar rests in the others)
    short = [i for i in range(len(bars0))
             if len({b[i].filled >= b[i].nominal for _, b in parts if b[i].notes}) > 1]
    if short:
        who = sorted({n for n, b in parts for i in short if b[i].notes and b[i].filled < b[i].nominal})
        warnings.append({"kind": "short", "count": len(short), "parts": who, "first": num.label(short[0]),
                         "app_bar": short[0] + 1,
                         "text": f"{len(short)} bar(s) are short in some parts only ({', '.join(who)}), first at bar "
                                 f"{num.label(short[0])} (app bar {short[0] + 1})"})
    return problems, warnings


# ---------- layer 2 ----------

def _shared(tok: str, t: Fraction, bars: list[Bar]) -> bool:
    return any(tk == tok and abs(tt - t) <= MARK_TOLERANCE for b in bars for tk, tt in b.marks)


def _segments(row: list[tuple[int, int]]) -> list[tuple[int, int, int, int, int]]:
    """Bar ranges where the score (marks that fit only when shifted, minus marks that fit only in place)
    is at least MIN_SCORE: (first, last, score, fit shifted, fit in place). Disjoint, best first."""
    row = list(row)
    out = []
    while True:
        best = cur = 0
        start = bs = be = 0
        for i, (w, l) in enumerate(row):
            if cur <= 0:
                cur, start = 0, i
            cur += w - l
            if cur > best:
                best, bs, be = cur, start, i
        if best < MIN_SCORE:
            return out
        out.append((bs, be, best, sum(w for w, _ in row[bs:be + 1]), sum(l for _, l in row[bs:be + 1])))
        for i in range(bs, be + 1):
            row[i] = (0, 0)


def check_music(parts: list[tuple[str, list[Bar]]]) -> tuple[list[dict], list[dict]]:
    """Layer 2. Run it only when layer 1 has no problems (the parts must have the same bars).

    Markings test: dynamics, hairpins, pizz./arco and similar words, and fermatas are usually in the same bar
    in all parts. When the markings of one part fit the other parts better 1 or 2 bars away, the notes of that
    part are probably in the wrong bars. Returns (suspects, coverage)."""
    bars0 = parts[0][1]
    num = Numbering(bars0, [b for _, b in parts])
    suspects, coverage = [], []
    for xi, (xname, xb) in enumerate(parts):
        others = [b for k, (_, b) in enumerate(parts) if k != xi]
        total = shared_total = 0
        unchecked = []
        played = checked = 0
        for s, e in num.mvts:
            here = [[_shared(tok, t, [o[k] for o in others]) for tok, t in xb[k].marks] for k in range(s, e)]
            total += sum(len(h) for h in here)
            shared_total += sum(sum(h) for h in here)
            for lag in LAGS:
                row = []
                for k in range(s, e):
                    w = l = 0
                    if s <= k + lag < e:
                        for (tok, t), a in zip(xb[k].marks, here[k - s]):
                            b = _shared(tok, t, [o[k + lag] for o in others])
                            w += b and not a
                            l += a and not b
                    row.append((w, l))
                for first, last, score, fit_shifted, fit_here in _segments(row):
                    suspects.append({
                        "part": xname, "from": num.label(s + first), "to": num.label(s + last),
                        "app_bars": [s + first + 1, s + last + 1], "shift": lag, "score": score,
                        "marks_fit_shifted": fit_shifted, "marks_fit_in_place": fit_here,
                    })
            # which bars the test can see: a window of CHECK_WINDOW bars needs CHECK_MIN_MARKS shared marks
            n = e - s
            ok = [False] * n
            for i in range(n):
                window = range(i, min(n, i + CHECK_WINDOW))
                if sum(sum(here[k]) for k in window) >= CHECK_MIN_MARKS:
                    for k in window:
                        ok[k] = True
            run = None
            for i in range(n + 1):
                has_notes = i < n and bool(xb[s + i].notes)
                if i < n and has_notes:
                    played += 1
                    checked += ok[i]
                if i < n and not ok[i]:
                    if run is None:
                        run = [i, i, 0]
                    run[1] = i
                    run[2] += has_notes
                elif run is not None:
                    if run[2] >= UNCHECKED_MIN:
                        unchecked.append({"from": num.label(s + run[0]), "to": num.label(s + run[1]),
                                          "app_bars": [s + run[0] + 1, s + run[1] + 1]})
                    run = None
        coverage.append({"part": xname, "marks": total, "shared": shared_total,
                         "checked_bars": checked, "played_bars": played, "unchecked": unchecked})
    suspects.sort(key=lambda x: -x["score"])
    return suspects, coverage


def check_parts(parts: list[tuple[str, list[Bar]]]) -> dict:
    """All checks. "movement_starts" = the app bar (from 1) where each movement starts."""
    starts = [s + 1 for s, _ in movements(parts[0][1])] if parts else []
    if len(parts) < 2:
        return {"problems": [], "warnings": [{"kind": "single", "text": "only one part: nothing to compare"}],
                "suspects": [], "coverage": [], "movement_starts": starts}
    problems, warnings = check_structure(parts)
    suspects, coverage = ([], []) if problems else check_music(parts)
    for c in coverage:
        if c["marks"] and c["shared"] < 0.5 * c["marks"]:
            warnings.append({"kind": "weak", "part": c["part"], "marks": c["marks"], "shared": c["shared"],
                             "text": f"{c['part']}: only {c['shared']} of {c['marks']} markings are also in another part "
                                     "at the same bar; the markings test is weak for this part"})
    return {"problems": problems, "warnings": warnings, "suspects": suspects, "coverage": coverage,
            "movement_starts": starts}


def report_lines(result: dict) -> list[str]:
    lines = [f"✗ {p['text']}" for p in result["problems"]] + [f"! {w['text']}" for w in result["warnings"]]
    for x in result["suspects"]:
        n = abs(x["shift"])
        lines.append(f"? {x['part']}, bars {x['from']}–{x['to']} (app {x['app_bars'][0]}–{x['app_bars'][1]}): "
                     f"probably {n} bar{'s' if n > 1 else ''} too {'early' if x['shift'] > 0 else 'late'} — "
                     f"{x['marks_fit_shifted']} markings fit the other parts {n} bar{'s' if n > 1 else ''} "
                     f"{'later' if x['shift'] > 0 else 'earlier'}, {x['marks_fit_in_place']} fit in place")
    for c in result.get("coverage", []):
        pct = round(100 * c["checked_bars"] / c["played_bars"]) if c["played_bars"] else 100
        line = f"  {c['part']}: markings test covers {pct}% of the bars with notes"
        if c["unchecked"]:
            line += ("; not checked: " + ", ".join(f"{u['from']}–{u['to']}" for u in c["unchecked"][:8])
                     + (f" and {len(c['unchecked']) - 8} more" if len(c["unchecked"]) > 8 else ""))
        lines.append(line)
    if not result["problems"] and not result["suspects"]:
        lines.append("✓ no problems and no suspect shifts")
    return lines


# ---------- compact text for a person or an agent ----------

def _dur(q: Fraction) -> str:
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def bar_text(bar: Bar) -> str:
    if not bar.notes:
        return "rest"
    by_start: dict[Fraction, list[Note]] = {}
    for n in bar.notes:
        by_start.setdefault(n.start, []).append(n)
    items = []
    for t in sorted(by_start):
        ns = by_start[t]
        names = "+".join(("~" if not n.onset else "") + n.name for n in ns)
        items.append(f"@{_dur(t)} {names} {_dur(max(n.end for n in ns) - t)}")
    return " | ".join(items)


def bars_text(parts: list[tuple[str, list[Bar]]], spec: str) -> str:
    """spec "M:A-B" (movement M, printed bars A-B) or "A-B" (app bars)."""
    bars0 = parts[0][1]
    num = Numbering(bars0, [b for _, b in parts])
    mvt, _, rng = spec.rpartition(":")
    a, _, b = rng.partition("-")
    try:
        a, b = int(a), int(b or a)
        if mvt and not 1 <= int(mvt) <= len(num.mvts):
            raise ValueError
        lo, hi = (num.index(int(mvt), a), num.index(int(mvt), b)) if mvt else (a - 1, b - 1)
    except ValueError:
        raise ValueError(f"bad --bars {spec!r}: use M:A-B (movement 1-{len(num.mvts)}, printed bars) or A-B (app bars)")
    longest = max(len(b) for _, b in parts)
    lo, hi = max(lo, 0), min(hi, longest - 1)
    width = max(len(n) for n, _ in parts)
    out = ["Times and lengths are in quarter notes from the start of the bar. "
           "'~' = tied from the previous note. '+' = notes of one chord."]
    if len({len(b) for _, b in parts}) > 1:
        out.append("The parts have different bar counts: each row shows the bar at this position in that part. "
                   f"Bar labels come from {parts[0][0]}.")
    for i in range(lo, hi + 1):
        time = next(b[i].time for _, b in parts if i < len(b))
        out.append(f"bar {num.label(i)} (app {i + 1}), {time}")
        for name, bars in parts:
            row = bar_text(bars[i]) if i < len(bars) else f"(no bar here: this part has only {len(bars)} bars)"
            out.append(f"  {name:<{width}}  {row}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--list":
        for w in works.list_works():
            print(w["slug"], "|", w["work"])
        return 0
    slug, rest = argv[0], argv[1:]
    parts = load_work(slug)
    if not parts:
        print(f"no work {slug!r} in {works.omr.DATA_DIR} (try --list)")
        return 2
    if "--bars" in rest:
        k = rest.index("--bars")
        try:
            print(bars_text(parts, rest[k + 1] if k + 1 < len(rest) else ""))
        except ValueError as e:
            print(e)
            return 2
        return 0
    result = check_parts(parts)
    if "--json" in rest:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Parts: {', '.join(n for n, _ in parts)}")
        for line in report_lines(result):
            print(line)
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
