"""Checks for check_alignment.py on the CC0 Haydn example: a clean score, shifted parts, structure errors,
the compact bar text, and the check_import.py output.

  uv run python tests/test_check_alignment.py
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="sp-align-test-")
os.environ["SP_DATA_DIR"] = DATA   # before omr is imported: it reads SP_DATA_DIR once
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import check_alignment as ca  # noqa: E402
import score_upload as su  # noqa: E402

failures = 0


def check(cond: bool, msg: str) -> None:
    global failures
    print(("ok   " if cond else "FAIL ") + msg)
    failures += not cond


def shifted(parts, pi: int, i0: int, i1: int, by: int = 1):
    """Part pi: `by` bars lost at i0, `by` empty bars added before i1. Bars i0..i1-1 then hold later music."""
    parts = [(n, list(b)) for n, b in parts]
    name, bars = parts[pi]
    fill = []
    for _ in range(by):
        x = copy.deepcopy(bars[i1 - 1])
        x.notes, x.marks = [], []
        fill.append(x)
    parts[pi] = (name, bars[:i0] + bars[i0 + by:i1] + fill + bars[i1:])
    return parts


r = su.import_example("haydn-op76-no3")
parts = ca.load_work(r["slug"])
check([n for n, _ in parts] == ["Violin I", "Violin II", "Viola", "Cello"], f"parts read: {[n for n, _ in parts]}")
check(all(len(b) == 531 for _, b in parts), "531 bars in each part")

# 1. the clean example: nothing to report
res = ca.check_parts(parts)
check(res["problems"] == [] and res["warnings"] == [] and res["suspects"] == [],
      f"clean score: no problems, warnings or suspects: {res['problems'] + res['warnings']} {res['suspects'][:2]}")
check(res["movement_starts"] == [1, 129, 238, 342], f"movement starts as app bars: {res['movement_starts']}")
check(len(res["coverage"]) == 4 and all(0 < c["checked_bars"] <= c["played_bars"] for c in res["coverage"]),
      "coverage for each part")
lines = ca.report_lines(res)
check(lines[-1].startswith("✓") and any("markings test covers" in x for x in lines), "report: coverage and ✓ line")
num = ca.Numbering(parts[0][1], [b for _, b in parts])
check(len(num.mvts) == 4 and num.pickup[0] and num.label(0) == "1:0" and num.index(1, 0) == 0,
      f"4 movements, movement 1 starts with a pickup bar 0: {num.mvts} {num.pickup}")

# 2. a part shifted by 1 or 2 bars over 32 bars of movement 1 (app bars 11-42)
for by in (1, 2):
    p = shifted(parts, 1, 10, 42, by)
    res = ca.check_parts(p)
    hit = [s for s in res["suspects"] if s["part"] == "Violin II" and s["shift"] == by
           and s["app_bars"][0] <= 42 and s["app_bars"][1] >= 11]
    check(res["problems"] == [] and hit, f"Violin II {by} bar(s) early: found {[(s['from'], s['to'], s['shift']) for s in res['suspects']]}")
    check(all(s["part"] == "Violin II" for s in res["suspects"]), "no suspect in the parts that are correct")
    text = "\n".join(ca.report_lines(res))
    check(f"probably {by} bar{'s' if by > 1 else ''} too early" in text, "report says how far and which way")

# a part that is late: its notes are 1 bar later than they should be
p = [(n, list(b)) for n, b in parts]
name, bars = p[2]
empty = copy.deepcopy(bars[10])
empty.notes, empty.marks = [], []
p[2] = (name, bars[:10] + [empty] + bars[10:41] + bars[42:])
res = ca.check_parts(p)
check(any(s["part"] == "Viola" and s["shift"] == -1 for s in res["suspects"]) and "too late" in "\n".join(ca.report_lines(res)),
      f"Viola 1 bar late: {[(s['part'], s['from'], s['shift']) for s in res['suspects']]}")

# 3. structure errors are problems, and layer 2 does not run
p = [(n, list(b)) for n, b in parts]
p[3] = (p[3][0], p[3][1][:100] + p[3][1][101:])
text = ca.bars_text(p, "526-531")
check("different bar counts" in text and "(no bar here: this part has only 530 bars)" in text and "bar 4:190 (app 531)" in text,
      "--bars on parts with different bar counts: no crash, the missing bar is marked")
res = ca.check_parts(p)
check(any(x["kind"] == "bars" and x["movement"] == 1 and x["counts"]["Cello"] == 127 and "different bar counts" in x["text"]
          for x in res["problems"]) and res["suspects"] == [],
      f"a missing bar: {res['problems']}")
p = [(n, list(b)) for n, b in parts]
b = copy.deepcopy(p[1][1][20])
b.time, b.nominal = "3/4", b.nominal * 3 / 4
p[1][1][20] = b
res = ca.check_parts(p)
check(any(x["kind"] == "time" and x["first"] == "1:20" and x["app_bar"] == 21 and x["got"] == "3/4" and x["want"] == "4/4"
          for x in res["problems"]), f"a wrong time signature: {res['problems']}")
check([w["kind"] for w in ca.check_parts(parts[:1])["warnings"]] == ["single"], "one part: nothing to compare")

# 4. compact text of bars
text = ca.bars_text(parts, "1:0-2")
check(text.count("bar 1:") == 3 and "(app 1)" in text and "Violin I" in text and "Cello" in text, "text of movement 1, bars 0-2")
check(ca.bars_text(parts, "5-5").count("bar ") == 1 and "(app 5)" in text + ca.bars_text(parts, "5-5"), "text of app bar 5")

# 5. CLI and check_import.py
env = {**os.environ, "SP_DATA_DIR": DATA}
out = subprocess.run([sys.executable, str(ROOT / "check_alignment.py"), r["slug"], "--json"], capture_output=True,
                     text=True, env=env, cwd=ROOT)
check(out.returncode == 0 and json.loads(out.stdout)["suspects"] == [], f"check_alignment.py --json: exit {out.returncode}")
ex = sorted(p for p in (ROOT / "examples" / "haydn-op76-no3").iterdir() if p.suffix in (".mxl", ".json"))
out = subprocess.run([sys.executable, str(ROOT / "check_import.py"), *map(str, ex)], capture_output=True, text=True, cwd=ROOT)
check(out.returncode == 0 and "Alignment:" in out.stdout and "✓ no problems and no suspect shifts" in out.stdout,
      f"check_import.py shows the alignment: exit {out.returncode}\n{out.stdout[-400:]}{out.stderr[-400:]}")
out = subprocess.run([sys.executable, str(ROOT / "check_import.py"), *map(str, ex), "--bars", "2:1-2"],
                     capture_output=True, text=True, cwd=ROOT)
check(out.returncode == 0 and out.stdout.count("bar 2:") == 2, "check_import.py --bars")

shutil.rmtree(DATA, ignore_errors=True)
print("FAIL" if failures else "All check_alignment checks passed.")
sys.exit(1 if failures else 0)
