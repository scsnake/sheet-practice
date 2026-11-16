"""Checks for the ready package (export_ready.py, check_import.py --export) and the static site (build_static.py),
on the CC0 Haydn example.

  uv run python tests/test_export_ready.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="sp-ready-test-")
os.environ["SP_DATA_DIR"] = DATA   # before omr is imported: it reads SP_DATA_DIR once
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import export_ready as er  # noqa: E402
import score_upload as su  # noqa: E402
import works  # noqa: E402

failures = 0
TMP = Path(tempfile.mkdtemp(prefix="sp-ready-out-"))


def check(cond: bool, msg: str) -> None:
    global failures
    print(("ok   " if cond else "FAIL ") + msg)
    failures += not cond


# 1. the package of an imported work
r = su.import_example("haydn-op76-no3")
data = er.package(r["slug"], r)
z = zipfile.ZipFile(io.BytesIO(data))
check(sorted(z.namelist()) == sorted([er.MANIFEST, er.SCORE, er.TIMELINE, su.CONFIG_NAME]), f"files: {z.namelist()}")
m = json.loads(z.read(er.MANIFEST))
check(m["format"] == er.FORMAT and m["version"] == er.VERSION and m["slug"] == r["slug"] and m["work"] == r["work"], "manifest: format, slug, work")
check([p["part"] for p in m["parts"]] == ["Violin I", "Violin II", "Viola", "Cello"] and all(p["measures"] == 531 for p in m["parts"]),
      "manifest: parts and bar counts")
check(m["settings"].get("practice") == m["parts"][1]["xml_id"] and m["summary"]["practice"] == "Violin II", "practice part: Violin II")
check(m["summary"]["alignment"]["suspects"] == [] and m["summary"]["problems"] == 0 and m["summary"]["measures"] == 531,
      "summary: check and alignment")
check(set(m["summary"]) <= set(er.SUMMARY_KEYS), f"summary has only the app's fields: {sorted(m['summary'])}")
merged, _ = works.merged_cached(r["slug"])
check(z.read(er.SCORE) == merged, "the score is the merged score")
tl = json.loads(z.read(er.TIMELINE))
check(tl["nMeasures"] == 531 and tl["steps"], "the timeline is there")
check(all(p["xml_id"] in merged.decode() for p in m["parts"]), "part ids in the manifest are the ids in the score")
check(len(data) < len(merged) / 5, f"compressed: {len(data)} bytes")

# 2. without an import result: the summary is computed from the stored work
m2 = json.loads(zipfile.ZipFile(io.BytesIO(er.package(r["slug"]))).read(er.MANIFEST))
check(m2["summary"]["alignment"]["suspects"] == [] and m2["summary"]["practice"] == "Violin II" and m2["summary"]["files_used"] == [],
      "summary of a stored work")
try:
    er.package("No-Such-Work")
    check(False, "an unknown work fails")
except ValueError as e:
    check("no work" in str(e), "an unknown work fails")

# provenance in sheet-practice.json: one value for all parts, so different values name each part
items = [(None, {"part": "Violin I", "rights": "CC0", "source": "A"}), (None, {"part": "Viola", "rights": "CC-BY", "source": "A"})]
check(er.provenance(items) == {"rights": "Violin I: CC0; Viola: CC-BY", "source": "A"}, f"provenance per part: {er.provenance(items)}")
check(er.provenance(items[:1]) == {"rights": "CC0", "source": "A"}, "provenance of equal parts: the value")

# 3. the local app imports the same package (sheet-practice.json in it)
r2 = su.create([("haydn-ready.zip", io.BytesIO(data))])
check(r2["work"].startswith(r["work"]) and r2["parts"] == r["parts"] and r2["measures"] == 531 and r2["movements"] == 4,
      f"local import of a ready package: {r2['work']}, {r2['parts']}")
check(r2["practice"] == "Violin II" and r2["alignment"]["suspects"] == [] and r2["alignment"]["problems"] == [],
      "local import: practice part and alignment")

# 4. CLIs
env = {**os.environ, "SP_DATA_DIR": DATA}
out = subprocess.run([sys.executable, str(ROOT / "export_ready.py"), r["slug"], "-o", str(TMP / "a.zip")],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check(out.returncode == 0 and zipfile.is_zipfile(TMP / "a.zip") and "Ready package" in out.stdout, f"export_ready.py: {out.stdout}{out.stderr[-300:]}")
out = subprocess.run([sys.executable, str(ROOT / "export_ready.py"), "No-Such-Work", "-o", str(TMP / "b.zip")],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check(out.returncode == 2 and not (TMP / "b.zip").exists(), "export_ready.py on an unknown work: exit 2")
ex = sorted(p for p in (ROOT / "examples" / "haydn-op76-no3").iterdir() if p.suffix in (".mxl", ".json"))
out = subprocess.run([sys.executable, str(ROOT / "check_import.py"), *map(str, ex), "--export", str(TMP / "c.zip")],
                     capture_output=True, text=True, cwd=ROOT)
mc = json.loads(zipfile.ZipFile(TMP / "c.zip").read(er.MANIFEST)) if (TMP / "c.zip").is_file() else {}
check(out.returncode == 0 and "Exported:" in out.stdout and mc.get("summary", {}).get("files_used") == ["sq20428156.mxl"],
      f"check_import.py --export: exit {out.returncode}\n{out.stdout[-300:]}{out.stderr[-300:]}")

# 5. build_static.py
site = TMP / "site"
out = subprocess.run([sys.executable, str(ROOT / "build_static.py"), "-o", str(site)], capture_output=True, text=True, cwd=ROOT)
index = (site / "index.html").read_text() if (site / "index.html").is_file() else ""
check(out.returncode == 0 and '<meta name="sp-mode" content="hosted">' in index and 'src="static/hosted.js"' in index,
      f"build_static.py: hosted index.html\n{out.stdout}{out.stderr[-300:]}")
check(not (site / "static" / "index.html").exists() and (site / "static" / "app.js").is_file(), "static files copied, index.html only at the top")
listed = json.loads((site / "examples" / "index.json").read_text()) if (site / "examples" / "index.json").is_file() else []
check([e["id"] for e in listed] == ["haydn-op76-no3"] and zipfile.is_zipfile(site / "examples" / "haydn-op76-no3.zip"), "example as a ready package")
check(not [f for f in site.rglob("*") if f.suffix in (".mxl", ".musicxml", ".pdf")], "no loose score files in the site")
out = subprocess.run([sys.executable, str(ROOT / "build_static.py"), "-o", str(site)], capture_output=True, text=True, cwd=ROOT)
check(out.returncode == 0, "a rebuild into the same folder works")
other = TMP / "other"
other.mkdir()
(other / "keep.txt").write_text("x")
out = subprocess.run([sys.executable, str(ROOT / "build_static.py"), "-o", str(other)], capture_output=True, text=True, cwd=ROOT)
check(out.returncode == 2 and (other / "keep.txt").is_file(), "a folder that is not an output is not deleted")

shutil.rmtree(DATA, ignore_errors=True)
shutil.rmtree(TMP, ignore_errors=True)
print("FAIL" if failures else "All export_ready checks passed.")
sys.exit(1 if failures else 0)
