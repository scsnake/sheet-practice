"""網頁匯入總譜：多個 MusicXML 檔、ZIP 檔 → 選聲部、改名稱、調樂章順序 → 匯入後直接打開；取消會刪掉暫存。

樂譜由 tests/fixtures/make_demo.py 的算式產生（不是真的樂譜）。需要伺服器在 8777，而且 SP_DATA_DIR 指向測試用資料夾（tests/run_e2e.sh 會設好）。
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import WORK
import io, os, sys, tempfile, time, zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import make_demo  # noqa: E402

URL = "http://localhost:8777/"
data_dir = Path(os.environ.get("SP_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").expanduser()
stage_dir = data_dir / "_uploads"
MV_44 = make_demo.mv("Allegro", 4, 4, 120, 12, 0)   # 12 bars of 4/4
MV_38 = make_demo.mv("Scherzo", 3, 8, 116, 8, 2)    # 8 bars of 3/8

fails = []
def check(name, ok, detail=""):
    print(("OK  " if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

def movement(title, mv, first_bar=1):
    return make_demo.movement_xml(title, mv, first_bar, [], False)

def mxl(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        z.writestr("score.xml", xml)
    return buf.getvalue()

def stage_empty():
    return not stage_dir.exists() or not any(stage_dir.iterdir())

def open_upload(pg, files, work=""):
    pg.click("[data-pop=settings] [data-poptoggle]")
    pg.click("#btnUpload")
    pg.set_input_files("#fileInput", files)
    pg.fill("#upWork", work)
    pg.click("#doUpload")

def wait_open(pg, dlg):
    pg.wait_for_function(f"document.getElementById('{dlg}').open", timeout=60000)

def work_info(pg, slug):
    return next((w for w in pg.request.get(URL + "api/works").json() if w["slug"] == slug), None)

def first_beat_type(pg, piece_id):
    xml = pg.request.get(f"{URL}api/pieces/{piece_id}/score.musicxml").text()
    return xml.split("<beat-type>", 1)[1].split("<", 1)[0]

def wait_loaded(pg, slug):
    pg.wait_for_function(f"window.SP && SP.slug==='{slug}' && SP.ready && SP.nMeasures>0", timeout=120000)

with tempfile.TemporaryDirectory() as td, sync_playwright() as p:
    td = Path(td)
    (td / "mvt2.musicxml").write_text(movement("Import Test A", MV_44))
    (td / "mvt10.musicxml").write_text(movement("Import Test A", MV_38, 13))
    with zipfile.ZipFile(td / "scores.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Import B/mvt1.mxl", mxl(movement("Import Test B", MV_38)))
        z.writestr("Import B/mvt2.musicxml", movement("Import Test B", MV_44, 9))
        z.writestr("__MACOSX/Import B/._mvt1.mxl", b"junk")
        z.writestr("readme.txt", "not a score")
    (td / "page.pdf").write_bytes(b"%PDF-1.4")

    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    alerts = []
    pg.on("dialog", lambda d: (alerts.append(d.message), d.accept()))
    pg.add_init_script(f"localStorage.setItem('lastWork', '{WORK}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.goto(URL)
    wait_loaded(pg, WORK)

    # A. two MusicXML files: natural order (mvt2 before mvt10), pick 2 parts, rename one
    open_upload(pg, [str(td / "mvt10.musicxml"), str(td / "mvt2.musicxml")], work="Import Test A")
    wait_open(pg, "importDlg")
    labels = pg.locator("#imFiles li .label").all_inner_texts()
    check("A: files in natural order", len(labels) == 2 and labels[0].startswith("mvt2.") and labels[1].startswith("mvt10."), str(labels))
    checked = pg.eval_on_selector_all("#imParts tbody tr", "rs => rs.filter(r => r.querySelector('input[type=checkbox]').checked).map(r => r.dataset.name)")
    check("A: strings selected by default, flute not", checked == ["Violin 1", "Violin 2", "Viola", "Cello", "Contrabass"], str(checked))
    for name in ("Violin 1", "Violin 2", "Contrabass"):
        pg.uncheck(f'#imParts tr[data-name="{name}"] input[type=checkbox]')
    pg.fill('#imParts tr[data-name="Cello"] input[type=text]', "Violoncello")
    pg.fill('#imParts tr[data-name="Viola"] input[type=text]', "violoncello")
    check("A: duplicate names block the import", pg.is_disabled("#doImport") and "重複" in pg.inner_text("#imWarn"))
    pg.fill('#imParts tr[data-name="Viola"] input[type=text]', "Viola")
    check("A: import enabled again", pg.is_enabled("#doImport"))
    pg.click("#doImport")
    wait_open(pg, "importDoneDlg")
    report = pg.inner_text("#imReport")
    check("A: report is clean", "沒有發現問題" in report, report.replace("\n", " | "))
    pg.click("#importDoneDlg button")
    wait_loaded(pg, "Import-Test-A")
    w = work_info(pg, "Import-Test-A")
    check("A: parts and order", w and [x["part"] for x in w["parts"]] == ["Viola", "Violoncello"], str(w and [x["part"] for x in w["parts"]]))
    check("A: bar count", pg.evaluate("SP.nMeasures") == 20, str(pg.evaluate("SP.nMeasures")))
    check("A: mvt2 (4/4) is the first movement", w and first_beat_type(pg, w["parts"][0]["id"]) == "4")
    check("A: stage folder removed", stage_empty())

    # B. one ZIP: non-scores skipped, work name from the file, reorder the movements
    open_upload(pg, [str(td / "scores.zip")])
    wait_open(pg, "importDlg")
    check("B: work name from the file", pg.input_value("#imWork") == "Import Test B", pg.input_value("#imWork"))
    check("B: two scores, other entries skipped", pg.locator("#imFiles li").count() == 2 and "略過 2" in pg.inner_text("#imSkipped"),
          pg.inner_text("#imSkipped"))
    check("B: rights from the file", pg.input_value("#imRights") == "CC0-1.0")
    pg.click("#imFiles li:nth-child(1) button:has-text('↓')")
    labels = pg.locator("#imFiles li .label").all_inner_texts()
    check("B: reordered", labels[0].startswith("scores.zip/Import B/mvt2"), str(labels))
    pg.click("#doImport")
    wait_open(pg, "importDoneDlg")
    pg.click("#importDoneDlg button")
    wait_loaded(pg, "Import-Test-B")
    w = work_info(pg, "Import-Test-B")
    check("B: five string parts", w and [x["part"] for x in w["parts"]] == ["Violin I", "Violin II", "Viola", "Cello", "Contrabass"],
          str(w and [x["part"] for x in w["parts"]]))
    check("B: all parts 20 bars", w and {x["measures"] for x in w["parts"]} == {20}, str(w and [x.get("measures") for x in w["parts"]]))
    check("B: reordered first movement is 4/4", w and first_beat_type(pg, w["parts"][0]["id"]) == "4")

    # C. cancel removes the staged files
    open_upload(pg, [str(td / "mvt2.musicxml")])
    wait_open(pg, "importDlg")
    check("C: staged while the dialog is open", not stage_empty())
    pg.click("#importDlg button[value=cancel]")
    deadline = time.time() + 10
    while not stage_empty() and time.time() < deadline:
        pg.wait_for_timeout(200)
    check("C: cancel removes the stage folder", stage_empty())

    # D. PDF mixed with MusicXML is refused before any upload
    open_upload(pg, [str(td / "page.pdf"), str(td / "mvt2.musicxml")])
    pg.wait_for_timeout(300)
    check("D: mixed PDF and MusicXML refused", any("一次只能上傳一個" in a for a in alerts), str(alerts))
    pg.keyboard.press("Escape")

    # E. different rights per file: import blocked until the user fills in a value;
    #    while the import runs, Esc and Cancel do not close the dialog
    (td / "e1.musicxml").write_text(movement("Import Test E", MV_44))
    (td / "e2.musicxml").write_text(movement("Import Test E", MV_38, 13).replace("<rights>CC0-1.0</rights>", "<rights>CC-BY-4.0</rights>"))
    open_upload(pg, [str(td / "e1.musicxml"), str(td / "e2.musicxml")], work="Import Test E")
    wait_open(pg, "importDlg")
    check("E: no rights guessed when files differ", pg.input_value("#imRights") == "")
    check("E: conflict blocks the import", pg.is_disabled("#doImport") and "授權不一樣" in pg.inner_text("#imWarn"), pg.inner_text("#imWarn"))
    check("E: each file's rights shown", "CC0-1.0" in pg.inner_text("#imWarn") and "CC-BY-4.0" in pg.inner_text("#imWarn"))
    pg.fill("#imRights", "CC0-1.0 / CC-BY-4.0")
    check("E: filled rights allow the import", pg.is_enabled("#doImport"))
    held = []
    pg.route("**/api/score-uploads/*/import", lambda route: held.append(route))
    pg.click("#doImport")
    pg.wait_for_function("document.getElementById('doImport').disabled")
    deadline = time.time() + 10
    while not held and time.time() < deadline:
        pg.wait_for_timeout(100)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    check("E: Esc does not close the dialog during the import", pg.evaluate("document.getElementById('importDlg').open"))
    check("E: Cancel is disabled during the import", pg.is_disabled("#importDlg button[value=cancel]"))
    check("E: staged files kept during the import", not stage_empty())
    held and held[0].continue_()
    wait_open(pg, "importDoneDlg")
    pg.unroute("**/api/score-uploads/*/import")
    pg.click("#importDoneDlg button")
    w = work_info(pg, "Import-Test-E")
    check("E: import finished", w and len(w["parts"]) == 5, str(w and len(w["parts"])))

    for slug in ("Import-Test-A", "Import-Test-B", "Import-Test-E"):
        for x in (work_info(pg, slug) or {"parts": []})["parts"]:
            pg.request.delete(f"{URL}api/pieces/{x['id']}")
    b.close()

print("FAIL:", ", ".join(fails) if fails else "OK all") if fails else print("All score import checks passed.")
