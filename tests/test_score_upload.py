"""Checks for score_upload.py (default import, folder choice, sheet-practice.json, re-import, ZIP safety)
on synthetic demo movements.

  uv run python tests/test_score_upload.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="sp-upload-test-")
os.environ["SP_DATA_DIR"] = DATA   # before omr is imported: it reads SP_DATA_DIR once
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import import_parts  # noqa: E402
import make_demo  # noqa: E402
import score_upload as su  # noqa: E402
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
STRINGS = [s[0] for s in make_demo.STRINGS]   # part names in the demo scores: Violin 1 … Contrabass


def movement(mv: dict, first_bar: int = 1, work: str = "Upload Test", piano: bool = False, rests=()) -> bytes:
    return make_demo.movement_xml(work, mv, first_bar, list(rests), piano).encode()


def single_part(xml: bytes, name: str) -> bytes:
    """One instrument's file, cut from a demo movement (like a per-instrument folder)."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.musicxml"
        f.write_bytes(xml)
        return import_parts.build_part_score([f], f"^{name}$", name)


def zip_bytes(entries: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


def parts_of(work: str) -> list[str]:
    return [m["part"] for _, m in works.pieces_of(works.slug(work))]


def bars_of(work: str) -> list[int]:
    return [works.measure_count(p) for p, _ in works.pieces_of(works.slug(work))]


def first_beat_type(work: str) -> str:
    p = works.pieces_of(works.slug(work))[0][0]
    return xmlsafe.etree.parse(str(p / "score.musicxml"), xmlsafe.PARSER).getroot().findtext("part/measure/attributes/time/beat-type")


def records() -> list[str]:
    return sorted(p.name for p in su.IMPORTS_DIR.iterdir()) if su.IMPORTS_DIR.exists() else []


# 1. ZIP safety; the default import runs at once
evil = zip_bytes({
    "Score/mvt10.musicxml": movement(MV2, 13),
    "Score/mvt2.musicxml": movement(MV1),
    "../escape.musicxml": movement(MV1),
    "/abs.musicxml": movement(MV1),
    "__MACOSX/Score/._mvt2.musicxml": b"junk",
    "Score/.hidden.musicxml": movement(MV1),
    "readme.txt": b"hello",
})
r = su.create([("scores.zip", evil)])
check(r["files_used"] == ["scores.zip/Score/mvt2.musicxml", "scores.zip/Score/mvt10.musicxml"], f"natural order: {r['files_used']}")
check(not (Path(DATA).parent / "escape.musicxml").exists() and not Path("/abs.musicxml").exists(), "no file is written outside the record")
stored = sorted(p.name for p in (su.IMPORTS_DIR / r["import"]).iterdir())
check(stored == ["001-mvt10.musicxml", "002-mvt2.musicxml", "import.json", "manifest.json"], f"stored names: {stored}")
check(len(su.summary(r["import"])["skipped"]) == 5, "5 other ZIP entries skipped")
check(r["work"] == "Upload Test" and parts_of("Upload Test") == ["Violin I", "Violin II", "Viola", "Cello", "Contrabass"],
      f"default: string parts with standard names, work from the file: {parts_of('Upload Test')}")
check(bars_of("Upload Test") == [20] * 5 and first_beat_type("Upload Test") == "4", "default: both movements, mvt2 first")
check(r["problems"] == 0, f"check is clean: {r['report']}")
check(all(m.get("rights") == "CC0-1.0" and m.get("import") == r["import"] for _, m in works.pieces_of("Upload-Test")),
      "rights from the file; pieces know their import")

# 2. the same upload again: a new work, not mixed into the first one
r2 = su.create([("scores.zip", zip_bytes({"Score/mvt2.musicxml": movement(MV1), "Score/mvt10.musicxml": movement(MV2, 13)}))])
check(r2["work"] == "Upload Test (2)" and r2["renamed_from"] == "Upload Test", f"free name: {r2['work']}")
check(len(parts_of("Upload Test")) == 5, "first work unchanged")

# 2b. a long work name: the " (2)" suffix must survive the 60-character slug limit (no endless loop)
long = "L" * 70
la = su.create([("a.musicxml", io.BytesIO(movement(MV1, work=long)))])
lb = su.create([("b.musicxml", io.BytesIO(movement(MV1, work=long)))])
check(la["slug"] != lb["slug"] and lb["work"].endswith(" (2)") and len(parts_of(la["work"])) == 5,
      f"long name gets a free name: {lb['work'][-8:]!r} {lb['slug'][-6:]!r}")

# 3. a work folder with full scores, accompaniment files (other notes) and per-instrument files
folder = {}
for i, (mv, first) in enumerate(((MV1, 1), (MV2, 13)), start=1):
    full = movement(mv, first, work="Folder Work", piano=True)                 # 7 parts
    folder[f"Folder Work/Full/mvt{i}.musicxml"] = full
    folder[f"Folder Work/Acc/mvt{i}_strings.musicxml"] = movement(mv, first, work="Folder Work", rests=[(1, 4)])  # 6 parts, other notes
    for name in STRINGS:
        folder[f"Folder Work/Strings/mvt{i}_{name.replace(' ', '')}.musicxml"] = single_part(full, name)
r3 = su.create([("folder.zip", zip_bytes(folder))])
check(all("/Full/" in f for f in r3["files_used"]) and len(r3["files_used"]) == 2, f"full-score folder chosen: {r3['files_used']}")
check(r3["files_unused"] == 12, f"other folders not used: {r3['files_unused']}")
check(bars_of("Folder Work") == [20] * 5, f"each movement once: {bars_of('Folder Work')}")

# 4. only per-instrument files: each part from its own files
strings_only = {k.split("/", 1)[1]: v for k, v in folder.items() if "/Strings/" in k}
r4 = su.create([("strings.zip", zip_bytes(strings_only))])
check(r4["work"] == "Folder Work (2)" and len(r4["files_used"]) == 10, f"per-instrument files used: {len(r4['files_used'])}")
check(parts_of(r4["work"]) == ["Violin I", "Violin II", "Viola", "Cello", "Contrabass"] and bars_of(r4["work"]) == [20] * 5,
      f"per-instrument parts: {parts_of(r4['work'])} {bars_of(r4['work'])}")

# 5. sheet-practice.json in the ZIP sets the import (paths relative to its folder)
cfg = {"work": "Config Work", "rights": "custom", "files": ["Strings/mvt2_Viola.musicxml", "Strings/mvt1_Viola.musicxml"],
       "parts": [{"name": "Viola", "display": "Bratsche"}]}
r5 = su.create([("cfg.zip", zip_bytes({**folder, "Folder Work/sheet-practice.json": json.dumps(cfg).encode()}))])
check(r5["config_file"] == "Folder Work/sheet-practice.json", f"config found: {r5['config_file']}")
check(parts_of("Config Work") == ["Bratsche"] and bars_of("Config Work") == [20] and first_beat_type("Config Work") == "8",
      f"config parts, names and order: {parts_of('Config Work')} {first_beat_type('Config Work')}")
check(works.pieces_of("Config-Work")[0][1].get("rights") == "custom", "config rights recorded")
exported = su.export_config(r5["import"])
check(exported["files"] == ["Folder Work/Strings/mvt2_Viola.musicxml", "Folder Work/Strings/mvt1_Viola.musicxml"]
      and exported["parts"] == [{"name": "Viola", "display": "Bratsche"}], f"export: {exported}")
before = records()
expect_error(lambda: su.create([("bad.zip", zip_bytes({**folder, "sheet-practice.json": b'{"files": ["nope.musicxml"]}'}))]),
             "找不到檔案", "config with a missing file")
expect_error(lambda: su.create([("bad.zip", zip_bytes({**folder, "sheet-practice.json": b"{not json"}))]), "JSON", "broken config")
check(records() == before, "failed imports leave no record")

# 5b. "practice" in the config sets the work's default practice part (score name or app name)
for practice, label in (("Violin II", "app name"), ("Violin 2", "score name")):
    cfg_p = {"work": f"Practice {label}", "practice": practice}
    rp = su.create([("p.zip", zip_bytes({**{k: v for k, v in folder.items() if "/Full/" in k},
                                          "Folder Work/sheet-practice.json": json.dumps(cfg_p).encode()}))])
    vn2 = next(p for p, m in works.pieces_of(works.slug(rp["work"])) if m["part"] == "Violin II")
    st = works.read_settings(works.slug(rp["work"]))
    check(rp["practice"] == "Violin II" and st.get("practice") == works.part_xml_id(vn2.name),
          f"practice from the {label}: {rp['practice']!r} {st}")
check(su.export_config(rp["import"])["practice"] == "Violin II", "practice in the exported settings")
expect_error(lambda: su.create([("p.zip", zip_bytes({**{k: v for k, v in folder.items() if "/Full/" in k},
                                                     "sheet-practice.json": b'{"practice": "Oboe"}'}))]),
             "練習聲部", "practice part that is not imported")
plan = su.summary(rp["import"])["plan"]
rp2 = su.apply(rp["import"], {**plan, "practice": "Viola"})
viola = next(p for p, m in works.pieces_of(works.slug(rp2["work"])) if m["part"] == "Viola")
check(works.read_settings(works.slug(rp2["work"])).get("practice") == works.part_xml_id(viola.name)
      and rp2["practice_xml_id"] == works.part_xml_id(viola.name), "re-import changes the practice part to the new piece")

# 6. re-import with other settings replaces this import's parts
r6 = su.apply(r3["import"], {"work": "Folder Work Edited", "files": list(reversed(su.summary(r3["import"])["plan"]["files"])),
                             "parts": [{"name": "Violin 1", "display": "Violin I"}, {"name": "Piano", "display": "Piano"}]})
check(r6["replaced"] == 5 and parts_of("Folder Work") == [], "old parts removed")
check(parts_of("Folder Work Edited") == ["Violin I", "Piano"] and first_beat_type("Folder Work Edited") == "8",
      f"new parts and order: {parts_of('Folder Work Edited')}")
check(su.summary(r3["import"])["plan"]["work"] == "Folder Work Edited", "settings saved")
real_check = su.prepare.check_work
def broken_check(*a, **k):
    raise ValueError("check failed")
su.prepare.check_work = broken_check
expect_error(lambda: su.apply(r3["import"], {"work": "Folder Work Edited", "files": su.summary(r3["import"])["plan"]["files"],
                                             "parts": [{"name": "Viola", "display": "Viola"}]}), "check failed", "failed re-import")
su.prepare.check_work = real_check
check(parts_of("Folder Work Edited") == ["Violin I", "Piano"], "failed re-import puts the old parts back")

# 7. different rights per file: each file's value is recorded, nothing is guessed
other = movement(MV2, 13, work="Conflict").replace(b"<rights>CC0-1.0</rights>", b"<rights>CC-BY-4.0</rights>")
r7 = su.create([("mvt1.musicxml", io.BytesIO(movement(MV1, work="Conflict"))), ("mvt2.musicxml", io.BytesIO(other))])
rights = works.pieces_of("Conflict")[0][1].get("rights")
check(rights == "mvt1.musicxml: CC0-1.0; mvt2.musicxml: CC-BY-4.0", f"per-file rights: {rights!r}")
plan = su.summary(r7["import"])["plan"]
su.apply(r7["import"], {**plan, "rights": "see each movement"})
check(works.pieces_of("Conflict")[0][1].get("rights") == "see each movement", "a typed value replaces it")

# 8. rejected input
before = records()
expect_error(lambda: su.create([("notes.pdf", io.BytesIO(b"%PDF"))]), "不支援", "PDF is rejected here")
expect_error(lambda: su.create([("x.zip", zip_bytes({"a.txt": b"x"}))]), "沒有找到", "zip without scores")
expect_error(lambda: su.create([("x.zip", io.BytesIO(b"not a zip"))]), "ZIP", "broken zip")
old = xmlsafe.MAX_XML_BYTES
xmlsafe.MAX_XML_BYTES = 1000
expect_error(lambda: su.create([("big.zip", zip_bytes({"big.musicxml": b"<a/>" + b" " * 5000}))]), "超過", "oversized zip entry")
mxl = zip_bytes({"META-INF/container.xml": b'<container><rootfiles><rootfile full-path="s.xml"/></rootfiles></container>',
                 "s.xml": b"<a/>" + b" " * 5000})
expect_error(lambda: xmlsafe.read_mxl(mxl), "超過", "oversized score inside .mxl")
xmlsafe.MAX_XML_BYTES = old
check(records() == before, "rejected input leaves no record")
ids = su.summary(r7["import"])["plan"]["files"]
expect_error(lambda: su.apply(r7["import"], {"work": "X", "files": ids, "parts": [{"name": "Viola", "display": "A"}, {"name": "Cello", "display": "a"}]}),
             "重複", "duplicate display names")
expect_error(lambda: su.apply(r7["import"], {"work": "X", "files": ["../manifest.json"], "parts": [{"name": "Viola", "display": "A"}]}),
             "檔案清單", "unknown file id")
expect_error(lambda: su.apply(r7["import"], {"work": "X", "files": ids, "parts": [{"name": "Oboe", "display": "Oboe"}]}), "沒有聲部", "missing part")
expect_error(lambda: su.summary("../../etc"), "代碼", "bad id")
check(parts_of("X") == [], "failed re-imports create no pieces")

# 9. an additive meter (3+2) does not stop the import
try:
    su.create([("add.musicxml", io.BytesIO(movement(MV1, work="Additive").replace(b"<beats>4</beats>", b"<beats>3+2</beats>")))])
    ok = True
except Exception as e:  # noqa: BLE001
    ok = False
    print("   ", type(e).__name__, e)
check(ok and len(parts_of("Additive")) == 5, "3+2 meter imports")

# 10. a record goes away when its parts are deleted
for p, _ in works.pieces_of("Conflict"):
    shutil.rmtree(p)
su.remove_if_unused(r7["import"])
check(r7["import"] not in records(), "record removed with its last part")
(su.IMPORTS_DIR / ("0" * 32)).mkdir()
(su.IMPORTS_DIR / ("0" * 32) / "manifest.json").write_text("{}")
su.cleanup_orphans(now=time.time() + su.ORPHAN_SECONDS + 1)
check("0" * 32 not in records() and r["import"] in records(), "old record without parts removed, used one kept")

# 11. several movements in one file (as in OpenScore String Quartets): bar numbers start again after a final barline
one = xmlsafe.etree.fromstring(movement(MV1, work="One File"), xmlsafe.PARSER)
two = xmlsafe.etree.fromstring(movement(MV2, 13, work="One File"), xmlsafe.PARSER)
for p1, p2 in zip(one.findall("part"), two.findall("part")):
    last = p1.findall("measure")[-1]
    bl = xmlsafe.etree.SubElement(last, "barline", location="right")
    xmlsafe.etree.SubElement(bl, "bar-style").text = "light-heavy"
    for m in p2.findall("measure"):   # numbers start at 1 again
        p1.append(m)
rf = su.create([("whole.musicxml", io.BytesIO(xmlsafe.etree.tostring(one)))])
pdir = works.pieces_of(works.slug(rf["work"]))[0][0]
xml = (pdir / "score.musicxml").read_text()
check(xml.count('id="movement-') == 2 and works.measure_count(pdir) == 20, f"two movements found in one file: {xml.count('id=\"movement-')}")
repeat_only = xmlsafe.etree.fromstring(movement(MV1, work="Repeat"), xmlsafe.PARSER)
for prt in repeat_only.findall("part"):   # a final-style barline inside a movement, numbers go on: no new movement
    bl = xmlsafe.etree.SubElement(prt.findall("measure")[5], "barline", location="right")
    xmlsafe.etree.SubElement(bl, "bar-style").text = "light-heavy"
rr = su.create([("rep.musicxml", io.BytesIO(xmlsafe.etree.tostring(repeat_only)))])
xml = (works.pieces_of(works.slug(rr["work"]))[0][0] / "score.musicxml").read_text()
check(xml.count('id="movement-') == 1, "a barline without a new bar 1 is not a movement start")

# 12. the bundled example (examples/, CC0): 4 parts, 4 movements, Violin II as practice part
ex = su.examples()
check([e["id"] for e in ex] == ["haydn-op76-no3"], f"examples listed: {ex}")
rx = su.import_example("haydn-op76-no3")
pieces = works.pieces_of(rx["slug"])
check([m["part"] for _, m in pieces] == ["Violin I", "Violin II", "Viola", "Cello"], f"example parts: {[m['part'] for _, m in pieces]}")
check({works.measure_count(p) for p, _ in pieces} == {531} and rx["problems"] == 0, f"example bars and check: {rx['report'][-1:]}")
check(all((p / "score.musicxml").read_text().count('id="movement-') == 4 for p, _ in pieces), "example has 4 movements")
check(rx["practice"] == "Violin II" and all(m.get("rights") == "OpenScore (CC0)" for _, m in pieces), "example practice part and rights")
expect_error(lambda: su.import_example("../examples"), "沒有這個範例", "unknown example")

check(rx["measures"] == 531 and rx["movements"] == 4, f"result counts: {rx['measures']} bars, {rx['movements']} movements")
al = rx["alignment"]
check(al["problems"] == [] and al["suspects"] == [] and len(al["coverage"]) == 4 and al["movement_starts"] == [1, 129, 238, 342],
      f"example alignment: {al.get('problems')} {al.get('suspects')} {al.get('movement_starts')}")
real_parts = su.check_alignment.check_parts
su.check_alignment.check_parts = broken_check
try:
    rb = su.import_example("haydn-op76-no3")
finally:
    su.check_alignment.check_parts = real_parts
check(rb["alignment"] == {"error": "ValueError: check failed"} and rb["problems"] == 0 and len(rb["pieces"]) == 4,
      f"a broken alignment check does not fail the import: {rb['alignment']}")

# 13. progress reports: read each file once, build each part, then check; re-import reads again
seen: list[tuple[str, int, int]] = []
rp = su.create([("prog.zip", zip_bytes({"P/mvt1.musicxml": movement(MV1, work="Progress"), "P/mvt2.musicxml": movement(MV2, 13, work="Progress")}))],
               lambda *a: seen.append(a))
check(seen == [("read", 0, 2), ("read", 1, 2), ("read", 2, 2)] + [("build", i, 5) for i in range(5)] + [("check", 0, 1)],
      f"progress of a first import: {seen}")
seen.clear()
su.apply(rp["import"], {"work": "Progress", "files": su.summary(rp["import"])["plan"]["files"],
                        "parts": [{"name": "Violin 1", "display": "Violin I"}]}, lambda *a: seen.append(a))
check([x[0] for x in seen] == ["read", "read", "read", "build", "check"], f"progress of a re-import: {seen}")
check(rp["measures"] == 20 and rp["movements"] == 2, f"counts of two files: {rp['measures']}, {rp['movements']}")

import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
tok = "ab" * 16
try:
    server._progress("../x")
    check(False, "bad progress token rejected")
except HTTPException as e:
    check(e.status_code == 400, "bad progress token rejected")
mid = []
out = server._with_progress(tok, lambda report: (report("build", 1, 3), mid.append(server.import_progress(tok)), "done")[-1])
check(mid == [{"stage": "build", "done": 1, "total": 3}] and out == "done", f"progress visible during the import: {mid}")
check(server.import_progress(tok)["stage"] == "", "progress removed after the import")
try:
    server._with_progress(tok, lambda report: (report("read", 0, 1), su.import_example("nope"))[-1])
    check(False, "failed import gives 400")
except HTTPException as e:
    check(e.status_code == 400 and server.import_progress(tok)["stage"] == "", "failed import gives 400 and removes the progress")

shutil.rmtree(DATA, ignore_errors=True)
print("FAIL" if failures else "All score_upload checks passed.")
sys.exit(1 if failures else 0)
