"""Checks for reduce_orchestra.py and prepare.py on small synthetic files.

  uv run python tests/test_reduce_orchestra.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lxml import etree  # noqa: E402

import prepare  # noqa: E402
import reduce_orchestra as ro  # noqa: E402


def score(div: int, fifths: int, notes: str, transpose: int | None = None, beats=4, beat_type=4) -> str:
    tr = f"<transpose><chromatic>{transpose}</chromatic></transpose>" if transpose is not None else ""
    return (f'<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>X</part-name></score-part></part-list>'
            f'<part id="P1"><measure number="1"><attributes><divisions>{div}</divisions><key><fifths>{fifths}</fifths></key>'
            f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>{tr}</attributes>{notes}</measure></part></score-partwise>")


def note(step: str, octave: int, dur: int) -> str:
    return f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch><duration>{dur}</duration></note>"


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text)
    return path


def onsets(path: Path) -> tuple[int, list[tuple]]:
    root = etree.parse(str(path)).getroot()
    div = int(root.findtext(".//divisions"))
    out, pos = [], 0
    for e in root.find("part/measure"):
        if e.tag == "backup":
            pos -= int(e.findtext("duration"))
        elif e.tag == "note":
            dur = int(e.findtext("duration"))
            if e.find("pitch") is not None and e.find("chord") is None:
                out.append((pos, dur, e.findtext("pitch/step")))
            pos += dur if e.find("chord") is None else 0
    return div, out


def test_mixed_divisions_keep_exact_positions(td: Path) -> None:
    quarters = write(td, "a.musicxml", score(4, 0, note("C", 5, 16)))
    triplets = write(td, "b.musicxml", score(6, 0, note("E", 5, 2) + note("F", 5, 2) + note("G", 5, 2)
                                             + note("A", 5, 18)))
    out = td / "out.musicxml"
    ro.reduce_movement([quarters, triplets], out, "X", 6)
    div, notes = onsets(out)
    assert div % 3 == 0, f"divisions {div} cannot hold triplets"
    assert sum(d for _, d, _ in notes) == 4 * div, notes
    assert notes[1][0] * 3 == div, f"second triplet note is at {notes[1][0]} of {div}"


def test_bar_crossing_note_is_reported(td: Path) -> None:
    good = write(td, "good.musicxml", score(4, 0, note("C", 5, 16)))
    bad = write(td, "bad.musicxml", score(4, 0, note("C", 5, 12) + note("D", 5, 8)))
    assert ro.check_sources([good]) == (0, 0)
    assert ro.check_sources([bad]) == (1, 1)
    assert prepare.over_full(prepare.load(good).find("part")) == []
    assert prepare.over_full(prepare.load(bad).find("part")) == [1]


def test_key_is_concert_pitch(td: Path) -> None:
    clarinet = write(td, "cl.musicxml", score(4, 2, note("C", 5, 16), transpose=-2))
    horn = write(td, "hn.musicxml", score(4, 0, note("C", 5, 16), transpose=-7))
    strings = write(td, "st.musicxml", score(4, 0, note("C", 5, 16)))
    out = td / "out.musicxml"
    ro.reduce_movement([clarinet, horn], out, "X", 6, [strings])
    assert etree.parse(str(out)).findtext(".//fifths") == "0", "the strings give the key"
    ro.reduce_movement([clarinet], out, "X", 6)
    assert etree.parse(str(out)).findtext(".//fifths") == "0", "D major for B-flat clarinet sounds C major"


def main() -> None:
    for name, test in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as td:
                test(Path(td))
            print("OK", name)


if __name__ == "__main__":
    main()
