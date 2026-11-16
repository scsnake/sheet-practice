"""Build the hosted app: a static site with no server. Works live in each visitor's browser (IndexedDB).

The site imports only ready packages (export_ready.py, check_import.py --export). It does no conversion.
It also gets the examples of examples/ as ready packages.

  uv run python build_static.py              # writes dist/
  uv run python build_static.py -o site/     # another folder
  npx wrangler pages deploy dist             # for example: Cloudflare Pages (any static host is correct)

The output folder must be new, empty, or an earlier output of this script. Never put scores in it: the host
serves all files to everyone. Visitors import their own packages in their browser.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="sp-build-")
os.environ["SP_DATA_DIR"] = TMP   # before omr is imported: it reads SP_DATA_DIR once

import export_ready  # noqa: E402
import score_upload  # noqa: E402

ROOT = Path(__file__).resolve().parent
MARK = ".sheet-practice-static"   # marks an output folder of this script, so that a rebuild can delete it
SERVER_MODE = '<meta name="sp-mode" content="server">'


def build(out: Path) -> list[str]:
    if out.exists():
        if any(out.iterdir()) and not (out / MARK).is_file():
            raise ValueError(f"{out} is not empty and is not an output of build_static.py: use a new folder")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARK).write_text("Output of build_static.py. A rebuild deletes this folder.\n")
    shutil.copytree(ROOT / "static", out / "static", ignore=shutil.ignore_patterns(".*"))
    index = (out / "static" / "index.html").read_text(encoding="utf-8")
    if SERVER_MODE not in index:
        raise ValueError(f"static/index.html has no {SERVER_MODE}")
    (out / "index.html").write_text(index.replace(SERVER_MODE, '<meta name="sp-mode" content="hosted">'), encoding="utf-8")
    (out / "static" / "index.html").unlink()
    listed = []
    (out / "examples").mkdir()
    for ex in score_upload.examples():   # only examples/ (a verified free license, see each SOURCE.md)
        r = score_upload.import_example(ex["id"])
        export_ready.write(r["slug"], out / "examples" / f"{ex['id']}.zip", r)
        listed.append(ex)
    (out / "examples" / "index.json").write_text(json.dumps(listed, ensure_ascii=False, indent=1), encoding="utf-8")
    return [e["id"] for e in listed]


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv) or (argv and argv[0] != "-o") or len(argv) not in (0, 2):
        print(__doc__)
        return 2
    out = Path(argv[1]) if argv else ROOT / "dist"
    try:
        ids = build(out)
    except ValueError as e:
        print(f"FAILED: {e}")
        return 2
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Static site: {out} ({size / 1e6:.1f} MB), examples: {', '.join(ids) or '(none)'}")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
