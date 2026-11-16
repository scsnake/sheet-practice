"""Make a ready package: one ZIP that the hosted (static) app imports as it is, with no conversion in the browser.

The package holds the merged score of all parts, the playback timeline, the default settings and the import summary
(check report and alignment check). Make it after the work is prepared and checked (docs/AGENT_SCORE_GUIDE.md).

  uv run python check_import.py my-work.zip --export my-work-ready.zip       # trial import, check, then export
  SP_DATA_DIR=<data folder> uv run python export_ready.py <work-slug> -o my-work-ready.zip   # a work already imported

The package also has a sheet-practice.json, so the local app (./run.sh) can import the same ZIP.

Exit code: 0 = written; 2 = bad arguments or the work is not found.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import check_alignment
import prepare
import score_upload
import works

FORMAT = "sheet-practice-ready"
VERSION = 1
MANIFEST = "sheet-practice-ready.json"
SCORE = "score.musicxml"
TIMELINE = "timeline.json"
# What the app's import summary shows (static/app.js showImportNote); the other fields of an import result are server-only
SUMMARY_KEYS = ("work", "parts", "practice", "practice_xml_id", "measures", "movements", "files_used", "files_unused",
                "problems", "report", "alignment", "config_file", "renamed_from")


def work_summary(slug: str) -> dict:
    """The import summary of a work that is already imported: check report (no fixes) and alignment check."""
    report: list[str] = []
    problems = prepare.check_work(slug, fix=False, out=report.append)
    try:
        alignment = check_alignment.check_parts(check_alignment.load_work(slug))
    except Exception as e:  # noqa: BLE001  # same rule as the import: a failed check is shown, not hidden
        alignment = {"error": f"{type(e).__name__}: {e}"}
    items = works.pieces_of(slug)
    first = items[0][0] / "score.musicxml"
    return {"work": items[0][1]["work"], "parts": [m["part"] for _, m in items],
            "measures": max((works.measure_count(p) or 0 for p, _ in items), default=0),
            "movements": first.read_text(encoding="utf-8").count('id="movement-') if first.is_file() else 0,
            "files_used": [], "files_unused": 0, "problems": problems, "report": report, "alignment": alignment}


def provenance(items) -> dict:
    """rights and source for sheet-practice.json. The local import gives one value to all parts:
    when the parts differ, the value names each part, so no part gets the value of another part."""
    parts = [{"path": m["part"], "rights": m.get("rights", ""), "source": m.get("source", "")} for _, m in items]
    return {k: score_upload.combined_provenance(parts, k) for k in ("rights", "source")}


def package(slug: str, summary: dict | None = None) -> bytes:
    """The ready package of an imported work, as ZIP bytes. summary: the import result (score_upload.create)."""
    items = works.pieces_of(slug)
    if not items:
        raise ValueError(f"no work {slug!r} (check_alignment.py --list shows the works)")
    xml, _ = works.merged_cached(slug)
    timeline, _ = works.timeline_cached(slug)
    settings = works.read_settings(slug)
    summary = {k: v for k, v in (summary or work_summary(slug)).items() if k in SUMMARY_KEYS}
    work = items[0][1]["work"]
    summary["work"] = work
    by_xml_id = {works.part_xml_id(p.name): m["part"] for p, m in items}
    summary.setdefault("practice", by_xml_id.get(settings.get("practice"), ""))
    summary.setdefault("practice_xml_id", settings.get("practice", ""))
    manifest = {
        "format": FORMAT, "version": VERSION, "created": int(time.time()),
        "slug": slug, "work": work,
        "parts": [{"part": m["part"], "xml_id": works.part_xml_id(p.name), "measures": works.measure_count(p),
                   "rights": m.get("rights", ""), "source": m.get("source", "")} for p, m in items],
        "settings": settings,
        "summary": summary,
        "files": {"score": SCORE, "timeline": TIMELINE},
    }
    config = {"work": work, "files": [SCORE], "parts": [{"name": m["part"], "display": m["part"]} for _, m in items],
              "practice": summary.get("practice", ""), **provenance(items)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr(SCORE, xml)
        z.writestr(TIMELINE, timeline)
        z.writestr(score_upload.CONFIG_NAME, json.dumps(config, ensure_ascii=False, indent=2))
    return buf.getvalue()


def write(slug: str, out: Path, summary: dict | None = None) -> int:
    data = package(slug, summary)
    out.write_bytes(data)
    return len(data)


def main(argv: list[str]) -> int:
    if "-o" not in argv or argv.index("-o") + 1 >= len(argv) or any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 2
    k = argv.index("-o")
    out = Path(argv[k + 1])
    rest = argv[:k] + argv[k + 2:]
    if len(rest) != 1:
        print(__doc__)
        return 2
    try:
        size = write(rest[0], out)
    except ValueError as e:
        print(f"FAILED: {e}")
        return 2
    print(f"Ready package: {out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
