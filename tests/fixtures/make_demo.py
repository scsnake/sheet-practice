"""Generate two synthetic demo works, for first-run use and for the e2e tests.

Every note comes from the arithmetic below. Nothing is derived from a published score, so the
output carries no third-party rights. The generated files are still not committed.

  uv run python tests/fixtures/make_demo.py            # write into SP_DATA_DIR (default: data/)
  uv run python tests/fixtures/make_demo.py --force    # delete old demo works first

Demo Symphony: strings, 4 movements, 835 bars. Movement 3 is in 3/8 and starts at bar 361.
Demo Concerto: piano and strings, 3 movements. The strings rest in bars 1-30 and 125-140.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import import_parts  # noqa: E402
import omr  # noqa: E402
import prepare  # noqa: E402
import works  # noqa: E402

DIVISIONS = 4  # one quarter note
TYPES = {1: ("16th", 0), 2: ("eighth", 0), 3: ("eighth", 1), 4: ("quarter", 0), 6: ("quarter", 1),
         8: ("half", 0), 12: ("half", 1), 16: ("whole", 0)}
STEPS = "CDEFGAB"
SHARPS = "FCGDAEB"
FLATS = "BEADGCF"
CHORDS = [0, 3, 4, 0, 5, 3, 4, 0]  # scale degrees of the chord roots
TONES = [0, 2, 4, 2]               # chord tones, as diatonic steps above the root

# name, clef (sign, line), lowest register (diatonic steps above C0), note length in divisions
STRINGS = [
    ("Violin 1", "Violin I", ("G", 2), 35, 2),
    ("Violin 2", "Violin II", ("G", 2), 30, 4),
    ("Viola", "Viola", ("C", 3), 25, 4),
    ("Cello", "Cello", ("F", 4), 18, 8),
    ("Contrabass", "Contrabass", ("F", 4), 14, 8),
]


def pitch_xml(diatonic: int, fifths: int) -> str:
    step, octave = STEPS[diatonic % 7], diatonic // 7
    alter = 0
    if fifths > 0 and step in SHARPS[:fifths]:
        alter = 1
    if fifths < 0 and step in FLATS[:-fifths]:
        alter = -1
    alter_xml = f"<alter>{alter}</alter>" if alter else ""
    return f"<pitch><step>{step}</step>{alter_xml}<octave>{octave}</octave></pitch>"


def note_xml(diatonic: int, dur: int, fifths: int, voice: int = 1, staff: int | None = None) -> str:
    name, dots = TYPES[dur]
    staff_xml = f"<staff>{staff}</staff>" if staff else ""
    return (f"<note>{pitch_xml(diatonic, fifths)}<duration>{dur}</duration><voice>{voice}</voice>"
            f"<type>{name}</type>{'<dot/>' * dots}{staff_xml}</note>")


def rest_xml(length: int, voice: int = 1, staff: int | None = None) -> str:
    staff_xml = f"<staff>{staff}</staff>" if staff else ""
    return f'<note><rest measure="yes"/><duration>{length}</duration><voice>{voice}</voice>{staff_xml}</note>'


def run_of_notes(bar: int, length: int, unit: int, base: int, tonic: int, fifths: int, voice=1, staff=None) -> str:
    """Fill one bar with notes of `unit` divisions, on the chord of that bar."""
    root = tonic + CHORDS[bar // 2 % len(CHORDS)]
    out, pos, i = [], 0, 0
    while pos < length:
        dur = min(unit, length - pos)
        if dur not in TYPES:
            dur = max(d for d in TYPES if d <= dur)
        out.append(note_xml(base + (root - base) % 7 + TONES[i % len(TONES)], dur, fifths, voice, staff))
        pos += dur
        i += 1
    return "".join(out)


def attributes(mv: dict, clefs: list[tuple[str, int]]) -> str:
    staves = f"<staves>{len(clefs)}</staves>" if len(clefs) > 1 else ""
    clef_xml = "".join(
        f"<clef{f' number=\"{i}\"' if len(clefs) > 1 else ''}><sign>{s}</sign><line>{ln}</line></clef>"
        for i, (s, ln) in enumerate(clefs, start=1))
    return (f"<attributes><divisions>{DIVISIONS}</divisions><key><fifths>{mv['fifths']}</fifths></key>"
            f"<time><beats>{mv['beats']}</beats><beat-type>{mv['type']}</beat-type></time>{staves}{clef_xml}</attributes>")


def tempo_xml(text: str, bpm: int) -> str:
    return (f'<direction placement="above"><direction-type><words>{escape(text)}</words></direction-type>'
            f'<sound tempo="{bpm}"/></direction>')


def dynamic_xml(mark: str) -> str:
    return f'<direction placement="below"><direction-type><dynamics><{mark}/></dynamics></direction-type></direction>'


def carrier_part(pid: str, mv: dict) -> str:
    """Tempo marks sit in the top part only, as in real scores. import_parts.py copies them."""
    length = mv["beats"] * DIVISIONS * 4 // mv["type"]
    bars = []
    for b in range(mv["bars"]):
        head = attributes(mv, [("G", 2)]) + tempo_xml(mv["title"], mv["bpm"]) if b == 0 else ""
        if mv.get("change") and b == mv["change"][0]:
            head += tempo_xml("poco meno mosso", mv["change"][1])
        bars.append(f'<measure number="{b + 1}">{head}{rest_xml(length)}</measure>')
    return f'<part id="{pid}">{"".join(bars)}</part>'


def string_part(pid: str, spec: tuple, mv: dict, first_bar: int, rests: list[tuple[int, int]]) -> str:
    _, _, clef, base, unit = spec
    length = mv["beats"] * DIVISIONS * 4 // mv["type"]
    tonic = 4 * mv["fifths"] % 7
    bars = []
    for b in range(mv["bars"]):
        head = attributes(mv, [clef]) if b == 0 else ""
        if b % 16 == 0 and pid == "P2":
            head += dynamic_xml("f" if b % 32 == 0 else "p")
        bar_no = first_bar + b
        silent = any(lo <= bar_no <= hi for lo, hi in rests)
        body = rest_xml(length) if silent else run_of_notes(b, length, unit, base, tonic, mv["fifths"])
        bars.append(f'<measure number="{b + 1}">{head}{body}</measure>')
    return f'<part id="{pid}">{"".join(bars)}</part>'


def piano_part(pid: str, mv: dict) -> str:
    length = mv["beats"] * DIVISIONS * 4 // mv["type"]
    tonic = 4 * mv["fifths"] % 7
    bars = []
    for b in range(mv["bars"]):
        head = attributes(mv, [("G", 2), ("F", 4)]) if b == 0 else ""
        upper = run_of_notes(b, length, 2, 35, tonic, mv["fifths"], voice=1, staff=1)
        lower = run_of_notes(b, length, 4, 16, tonic, mv["fifths"], voice=5, staff=2)
        bars.append(f'<measure number="{b + 1}">{head}{upper}<backup><duration>{length}</duration></backup>{lower}</measure>')
    return f'<part id="{pid}">{"".join(bars)}</part>'


def movement_xml(work: str, mv: dict, first_bar: int, rests, with_piano: bool) -> str:
    names = [("Flute", "P1")] + [(s[0], f"P{i}") for i, s in enumerate(STRINGS, start=2)]
    if with_piano:
        names.append(("Piano", "P7"))
    part_list = "".join(f'<score-part id="{pid}"><part-name>{n}</part-name></score-part>' for n, pid in names)
    parts = [carrier_part("P1", mv)]
    parts += [string_part(f"P{i}", s, mv, first_bar, rests) for i, s in enumerate(STRINGS, start=2)]
    if with_piano:
        parts.append(piano_part("P7", mv))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="4.0">'
            f"<work><work-title>{escape(work)}</work-title></work><movement-title>{escape(mv['title'])}</movement-title>"
            '<identification><creator type="composer">sheet-practice demo</creator><rights>CC0-1.0</rights>'
            "<source>tests/fixtures/make_demo.py</source></identification>"
            f"<part-list>{part_list}</part-list>{''.join(parts)}</score-partwise>")


def mv(title, beats, typ, bpm, bars, fifths, change=None) -> dict:
    return dict(title=title, beats=beats, type=typ, bpm=bpm, bars=bars, fifths=fifths, change=change)


SYMPHONY = [mv("Allegro", 4, 4, 120, 220, -1), mv("Andante", 4, 4, 80, 140, 0, change=(70, 72)),
            mv("Scherzo", 3, 8, 116, 180, 2), mv("Finale", 4, 4, 132, 295, -1)]
CONCERTO = [mv("Moderato", 4, 4, 100, 200, -3), mv("Adagio", 4, 4, 66, 120, 0), mv("Rondo", 4, 4, 138, 180, -3)]


def build(work: str, movements: list[dict], rests, with_piano: bool, td: Path) -> None:
    files, first_bar = [], 1
    for i, m in enumerate(movements, start=1):
        f = td / f"{works.slug(work)}-{i}.musicxml"
        f.write_text(movement_xml(work, m, first_bar, rests, with_piano), encoding="utf-8")
        files.append(f)
        first_bar += m["bars"]
    specs = [(f"^{s[0]}$", s[1]) for s in STRINGS]
    if with_piano:
        specs.append(("^Piano$", "Piano"))
    for order, (pattern, display) in enumerate(specs, start=1):
        import_parts.import_part(work, display, files, pattern, order)
    print(f"{work}: {len(specs)} parts, {first_bar - 1} bars -> {omr.DATA_DIR}")
    prepare.check_work(works.slug(work), fix=True)  # remove repeated tempo text


def main() -> None:
    demo = [("Demo Symphony", SYMPHONY, [], False), ("Demo Concerto", CONCERTO, [(1, 30), (125, 140)], True)]
    with tempfile.TemporaryDirectory() as td:
        for work, movements, rests, with_piano in demo:
            existing = works.pieces_of(works.slug(work))
            if existing and "--force" not in sys.argv:
                print(f"{work}: already there (use --force to rebuild)")
                continue
            for pdir, _ in existing:
                shutil.rmtree(pdir)
            build(work, movements, rests, with_piano, Path(td))


if __name__ == "__main__":
    main()
