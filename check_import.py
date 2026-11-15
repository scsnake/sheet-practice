"""Trial import: show what「＋ 匯入樂譜」would do with a ZIP or score files, without changing your data.

The import runs in a new temporary data folder, which is deleted at the end.

  uv run python check_import.py my-work.zip
  uv run python check_import.py mvt1.mxl mvt2.mxl sheet-practice.json
  uv run python check_import.py my-work.zip --json      # machine-readable result

Exit code: 0 = imported, no problems; 1 = imported, but prepare.py found problems; 2 = the import failed.
Warnings (lines that start with "!", for example a missing license) do not change the exit code.
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

import score_upload  # noqa: E402
import works  # noqa: E402


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
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
    }
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
    return 1 if out["problems"] else 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
