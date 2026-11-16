"""Trial import: show what「＋ 匯入樂譜」would do with a ZIP or score files, without changing your data.

The import runs in a new temporary data folder, which is deleted at the end.

  uv run python check_import.py my-work.zip
  uv run python check_import.py mvt1.mxl mvt2.mxl sheet-practice.json
  uv run python check_import.py my-work.zip --json      # machine-readable result
  uv run python check_import.py my-work.zip --bars 1:50-65
      # all parts of movement 1, bars 50-65, as compact text (to look at an alignment suspect)
  uv run python check_import.py my-work.zip --export my-work-ready.zip
      # also write the ready package for the hosted app (export_ready.py)

It also checks that the parts line up bar by bar (check_alignment.py).

Exit code: 0 = imported, no problems; 1 = imported, but prepare.py or the alignment check found problems;
2 = the import failed.
Warnings ("!", for example a missing license) and alignment suspects ("?") do not change the exit code.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="sp-check-")
os.environ["SP_DATA_DIR"] = TMP   # before omr is imported: it reads SP_DATA_DIR once

import check_alignment  # noqa: E402
import export_ready  # noqa: E402
import score_upload  # noqa: E402
import works  # noqa: E402


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    bars = export = None
    if "--export" in argv:
        k = argv.index("--export")
        if k + 1 >= len(argv):
            print("--export needs a file name, for example my-work-ready.zip")
            return 2
        export = Path(argv[k + 1])
        argv = argv[:k] + argv[k + 2:]
    if "--bars" in argv:
        k = argv.index("--bars")
        if k + 1 >= len(argv):
            print("--bars needs a range, for example 1:50-65")
            return 2
        bars = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    paths = [Path(a) for a in argv if a != "--json"]
    if not paths or any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 2
    streams = [p.open("rb") for p in paths]
    try:
        result = score_upload.create([(p.name, f) for p, f in zip(paths, streams)])
    except (ValueError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if as_json else f"FAILED: {e}")
        return 2
    finally:
        for f in streams:
            f.close()
    parts = []
    for pdir, meta in works.pieces_of(result["slug"]):
        xml = (pdir / "score.musicxml").read_text(encoding="utf-8")
        parts.append({"part": meta["part"], "bars": works.measure_count(pdir), "movements": xml.count('id="movement-'),
                      "rights": meta.get("rights", ""), "source": meta.get("source", "")})
    if bars:
        aligned = check_alignment.load_work(result["slug"])
        try:
            print(check_alignment.bars_text(aligned, bars))
        except ValueError as e:
            print(e)
            return 2
        return 0
    alignment = result["alignment"]
    if "error" in alignment:
        alignment = {"problems": [{"kind": "error", "text": "the alignment check failed: " + alignment["error"]}],
                     "warnings": [], "suspects": [], "coverage": []}
    out = {
        "ok": True,
        "work": result["renamed_from"] or result["work"],
        "config_file": result["config_file"],
        "files_used": result["files_used"],
        "files_unused": result["files_unused"],
        "parts": parts,
        "practice": result["practice"],
        "problems": result["problems"],
        "warnings": [line.strip() for line in result["report"] if line.strip().startswith("!")],
        "report": result["report"],
        "alignment": alignment,
    }
    if export:
        size = export_ready.write(result["slug"], export, result)
        out["export"] = str(export)
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Work:      {out['work']}")
        print(f"Settings:  {out['config_file'] or '(defaults, no sheet-practice.json)'}")
        print(f"Files:     {len(out['files_used'])} used, {out['files_unused']} not used")
        for f in out["files_used"]:
            print(f"           {f}")
        print(f"Practice:  {out['practice'] or '(not set: the first part)'}")
        for p in parts:
            print(f"Part:      {p['part']:<14} {p['bars']} bars, {p['movements']} movement(s)")
        if parts:
            print(f"Rights:    {parts[0]['rights'] or '(none)'}")
            print(f"Source:    {parts[0]['source'] or '(none)'}")
        print("Check:")
        for line in out["report"]:
            print(f"  {line.strip()}")
        print("Alignment:")
        for line in check_alignment.report_lines(alignment):
            print(f"  {line}")
        if export:
            print(f"Exported:  {export} ({size / 1e6:.1f} MB), the ready package for the hosted app")
    return 1 if out["problems"] or alignment["problems"] else 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
