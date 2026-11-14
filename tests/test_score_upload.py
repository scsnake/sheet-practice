"""Checks for score_upload.py (staging, ZIP safety, part choice, import) on synthetic demo movements.

  uv run python tests/test_score_upload.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="sp-upload-test-")
os.environ["SP_DATA_DIR"] = DATA   # before omr is imported: it reads SP_DATA_DIR once
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import make_demo  # noqa: E402
import score_upload  # noqa: E402
import works  # noqa: E402
import xmlsafe  # noqa: E402

failures = 0


def check(cond: bool, msg: str) -> None:
    global failures
    print(("ok   " if cond else "FAIL ") + msg)
    failures += not cond


def expect_error(fn, text: str, msg: str) -> None:
    try:
        fn()
    except ValueError as e:
        check(text in str(e), f"{msg}: {e}")
    else:
        check(False, f"{msg}: no error")


MV1 = make_demo.mv("Allegro", 4, 4, 120, 12, 0)
MV2 = make_demo.mv("Scherzo", 3, 8, 116, 8, 2)


def movement(mv: dict, first_bar: int = 1) -> bytes:
    return make_demo.movement_xml("Upload Test", mv, first_bar, [], False).encode()


def zip_bytes(entries: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


def staged_names(token: str) -> set[str]:
    return {p.name for p in (score_upload.STAGE_DIR / token).iterdir()}


# 1. ZIP: only MusicXML is kept, and no ZIP path is used on disk
evil = zip_bytes({
    "Score/mvt10.musicxml": movement(MV2, 13),
    "Score/mvt2.musicxml": movement(MV1),
    "../escape.musicxml": movement(MV1),
    "/abs.musicxml": movement(MV1),
    "__MACOSX/Score/._mvt2.musicxml": b"junk",
    "Score/.hidden.musicxml": movement(MV1),
    "readme.txt": b"hello",
})
s = score_upload.stage([("scores.zip", evil)])
labels = [f["label"] for f in s["files"]]
check(labels == ["scores.zip/Score/mvt2.musicxml", "scores.zip/Score/mvt10.musicxml"], f"zip keeps 2 scores, natural order: {labels}")
check(len(s["skipped"]) == 5, f"zip skips 5 other entries: {s['skipped']}")
check(not (Path(DATA).parent / "escape.musicxml").exists() and not Path("/abs.musicxml").exists(), "no file is written outside the stage folder")
check(staged_names(s["token"]) == {"manifest.json", "001-mvt10.musicxml", "002-mvt2.musicxml"}, f"stored names: {sorted(staged_names(s['token']))}")
parts = {p["name"]: p for p in s["parts"]}
check(set(parts) == {"Flute", "Violin 1", "Violin 2", "Viola", "Cello", "Contrabass"}, f"parts listed: {list(parts)}")
check([p["name"] for p in s["parts"] if p["selected"]] == ["Violin 1", "Violin 2", "Viola", "Cello", "Contrabass"], "strings are selected, flute is not")
check(parts["Violin 1"]["display"] == "Violin I", "display name guess: Violin 1 -> Violin I")
check(s["rights"] == "CC0-1.0" and s["work"] == "Upload Test", "rights and work title come from the file")

# 2. import: chosen parts, chosen order, chosen names
order = [f["id"] for f in reversed(s["files"])]   # Scherzo (3/8) first
r = score_upload.run_import(s["token"], "Upload Test", order,
                            [{"name": "Viola", "display": "Viola"}, {"name": "Cello", "display": "Violoncello"}])
pieces = works.pieces_of(works.slug("Upload Test"))
check([m["part"] for _, m in pieces] == ["Viola", "Violoncello"], f"imported parts in order: {[m['part'] for _, m in pieces]}")
bars = [works.measure_count(p) for p, _ in pieces]
check(bars == [20, 20], f"bars per part: {bars}")
first_time = xmlsafe.etree.parse(str(pieces[0][0] / "score.musicxml"), xmlsafe.PARSER).getroot().findtext("part/measure/attributes/time/beat-type")
check(first_time == "8", "the reordered first movement (3/8) comes first")
check(r["problems"] == 0, f"prepare check is clean: {r['report']}")
check(all(m.get("rights") == "CC0-1.0" for _, m in pieces), "rights recorded from the file")
check(not (score_upload.STAGE_DIR / s["token"]).exists(), "stage folder removed after import")

# 3. a second import into the same work goes after the existing parts
s2 = score_upload.stage([("mvt2.musicxml", io.BytesIO(movement(MV1))), ("mvt10.musicxml", io.BytesIO(movement(MV2, 13)))])
check([f["label"] for f in s2["files"]] == ["mvt2.musicxml", "mvt10.musicxml"], "multi-file upload, natural order")
r2 = score_upload.run_import(s2["token"], "Upload Test", [f["id"] for f in s2["files"]],
                             [{"name": "Contrabass", "display": "Contrabass"}], rights="CC0-1.0", source="test")
check(r2["added_to_existing"], "second import reports the existing work")
check([m["part"] for _, m in works.pieces_of(works.slug("Upload Test"))] == ["Viola", "Violoncello", "Contrabass"], "new part goes last")

# 4. rejected input
expect_error(lambda: score_upload.stage([("notes.pdf", io.BytesIO(b"%PDF"))]), "不支援", "PDF is rejected here")
expect_error(lambda: score_upload.stage([("x.zip", zip_bytes({"a.txt": b"x"}))]), "沒有找到", "zip without scores")
expect_error(lambda: score_upload.stage([("x.zip", io.BytesIO(b"not a zip"))]), "ZIP", "broken zip")
old = xmlsafe.MAX_XML_BYTES
xmlsafe.MAX_XML_BYTES = 1000
expect_error(lambda: score_upload.stage([("big.zip", zip_bytes({"big.musicxml": b"<a/>" + b" " * 5000}))]), "超過", "oversized zip entry")
mxl = zip_bytes({"META-INF/container.xml": b'<container><rootfiles><rootfile full-path="s.xml"/></rootfiles></container>',
                 "s.xml": b"<a/>" + b" " * 5000})
expect_error(lambda: xmlsafe.read_mxl(mxl), "超過", "oversized score inside .mxl")
xmlsafe.MAX_XML_BYTES = old
leftovers = [p.name for p in score_upload.STAGE_DIR.iterdir()]
check(leftovers == [], f"failed uploads leave no stage folder: {leftovers}")

s3 = score_upload.stage([("mvt2.musicxml", io.BytesIO(movement(MV1)))])
ids = [f["id"] for f in s3["files"]]
expect_error(lambda: score_upload.run_import(s3["token"], "X", ids, [{"name": "Viola", "display": "A"}, {"name": "Cello", "display": "a"}]),
             "重複", "duplicate display names")
expect_error(lambda: score_upload.run_import(s3["token"], "X", ["../manifest.json"], [{"name": "Viola", "display": "A"}]),
             "順序", "unknown file id")
expect_error(lambda: score_upload.run_import(s3["token"], "X", ids, [{"name": "Oboe", "display": "Oboe"}]), "沒有聲部", "missing part")
expect_error(lambda: score_upload.run_import("../../etc", "X", ids, []), "代碼", "bad token")
check(works.pieces_of(works.slug("X")) == [], "failed imports create no pieces")
score_upload.discard(s3["token"])
check(not (score_upload.STAGE_DIR / s3["token"]).exists(), "discard removes the stage folder")

# 5. stale stage folders are removed
s4 = score_upload.stage([("mvt2.musicxml", io.BytesIO(movement(MV1)))])
score_upload.cleanup_stale(now=__import__("time").time() + score_upload.STALE_SECONDS + 1)
check(not (score_upload.STAGE_DIR / s4["token"]).exists(), "stale stage folder removed")

# 6. files that state different rights: no value is guessed, and the user must give one
other = movement(MV2, 13).replace(b"<rights>CC0-1.0</rights>", b"<rights>CC-BY-4.0</rights>")
s5 = score_upload.stage([("mvt1.musicxml", io.BytesIO(movement(MV1))), ("mvt2.musicxml", io.BytesIO(other))])
check(s5["rights_conflict"] and s5["rights"] == "", f"rights conflict found, no value guessed: {s5['rights']!r}")
check(not s5["source_conflict"] and s5["source"] == "tests/fixtures/make_demo.py", "same source in both files is kept")
check([f["rights"] for f in s5["files"]] == ["CC0-1.0", "CC-BY-4.0"], "each file's rights are listed")
ids5 = [f["id"] for f in s5["files"]]
expect_error(lambda: score_upload.run_import(s5["token"], "Conflict", ids5, [{"name": "Viola", "display": "Viola"}]),
             "授權不一樣", "import refused without a rights value")
score_upload.run_import(s5["token"], "Conflict", ids5, [{"name": "Viola", "display": "Viola"}], rights="see each movement")
check([m.get("rights") for _, m in works.pieces_of(works.slug("Conflict"))] == ["see each movement"], "the user's rights value is recorded")

# 7. a failure in the check after the import removes the new pieces, and keeps the upload for a retry
s6 = score_upload.stage([("mvt1.musicxml", io.BytesIO(movement(MV1)))])
real_check = score_upload.prepare.check_work
def broken_check(*a, **k):
    raise ValueError("check failed")
score_upload.prepare.check_work = broken_check
expect_error(lambda: score_upload.run_import(s6["token"], "Rollback", [f["id"] for f in s6["files"]],
                                             [{"name": "Viola", "display": "Viola"}, {"name": "Cello", "display": "Cello"}]),
             "check failed", "check failure is reported")
score_upload.prepare.check_work = real_check
check(works.pieces_of(works.slug("Rollback")) == [], "check failure leaves no pieces")
check((score_upload.STAGE_DIR / s6["token"]).exists(), "check failure keeps the upload for a retry")
score_upload.discard(s6["token"])

# 8. an additive meter (3+2) does not stop the import
add = movement(MV1).replace(b"<beats>4</beats>", b"<beats>3+2</beats>")
s7 = score_upload.stage([("add.musicxml", io.BytesIO(add))])
try:
    score_upload.run_import(s7["token"], "Additive", [f["id"] for f in s7["files"]], [{"name": "Viola", "display": "Viola"}])
    ok = True
except Exception as e:  # noqa: BLE001
    ok = False
    print("   ", type(e).__name__, e)
check(ok and len(works.pieces_of(works.slug("Additive"))) == 1, "3+2 meter imports")

__import__("shutil").rmtree(DATA, ignore_errors=True)
print("FAIL" if failures else "All score_upload checks passed.")
sys.exit(1 if failures else 0)
