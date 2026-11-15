"""網頁匯入總譜：選檔案就用預設值直接匯入；之後在「匯入設定」裡改聲部、檔案、順序、名稱，重新匯入。

樂譜由 tests/fixtures/make_demo.py 的算式產生（不是真的樂譜）。需要伺服器在 8777，而且 SP_DATA_DIR 指向測試用資料夾（tests/run_e2e.sh 會設好）。
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import WORK
import io, json, os, sys, tempfile, time, zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import make_demo  # noqa: E402
import import_parts  # noqa: E402

URL = "http://localhost:8777/"
MV_44 = make_demo.mv("Allegro", 4, 4, 120, 12, 0)   # 12 bars of 4/4
MV_38 = make_demo.mv("Scherzo", 3, 8, 116, 8, 2)    # 8 bars of 3/8
STRINGS = [s[0] for s in make_demo.STRINGS]

fails = []
def check(name, ok, detail=""):
    print(("OK  " if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

def movement(title, mv, first_bar=1, piano=False, rests=()):
    return make_demo.movement_xml(title, mv, first_bar, list(rests), piano)

def single_part(xml: str, name: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.musicxml"
        f.write_text(xml)
        return import_parts.build_part_score([f], f"^{name}$", name)

def write_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)

def work_info(pg, slug):
    return next((w for w in pg.request.get(URL + "api/works").json() if w["slug"] == slug), None)

def first_beat_type(pg, piece_id):
    xml = pg.request.get(f"{URL}api/pieces/{piece_id}/score.musicxml").text()
    return xml.split("<beat-type>", 1)[1].split("<", 1)[0]

def wait_loaded(pg, slug, bars=None):
    # SP.slug changes at once, but SP.ready and SP.parts stay from the old work until the new one is drawn.
    # Two works can have the same bar count, so wait until the page shows exactly this work's parts.
    deadline = time.time() + 120
    while not work_info(pg, slug) and time.time() < deadline:
        pg.wait_for_timeout(200)
    ids = [x["xmlId"] for x in work_info(pg, slug)["parts"]]
    cond = f"SP.nMeasures==={bars}" if bars else "SP.nMeasures>0"
    pg.wait_for_function(f"window.SP && SP.slug==='{slug}' && SP.ready && {cond} && "
                         f"JSON.stringify(SP.parts.map(p => p.xmlId))==={json.dumps(json.dumps(ids, separators=(',', ':')))}",
                         timeout=120000)

def practice_xml_id(pg):
    return pg.evaluate("SP.parts[SP.practice] && SP.parts[SP.practice].xmlId")

def part_xml_id(w, name):
    return next(x["xmlId"] for x in w["parts"] if x["part"] == name)

def open_settings_panel(pg):
    if not pg.is_visible("#btnUpload"):
        pg.click("[data-pop=settings] [data-poptoggle]")

def pick_files(pg, files):
    """按「＋ 匯入樂譜」，在檔案選擇視窗選檔案（和使用者一樣，不經過別的視窗）。"""
    open_settings_panel(pg)
    with pg.expect_file_chooser() as fc:
        pg.click("#btnUpload")
    fc.value.set_files(files)

def wait_note(pg, text, timeout=120000):
    pg.wait_for_function(f"!document.getElementById('importNote').classList.contains('hidden') && "
                         f"document.getElementById('importNoteText').textContent.includes({json.dumps(text)})", timeout=timeout)
    return pg.inner_text("#importNoteText")

with tempfile.TemporaryDirectory() as td, sync_playwright() as p:
    td = Path(td)
    folder = {}
    for i, (mv, first) in enumerate(((MV_44, 1), (MV_38, 13)), start=1):
        full = movement("Import A", mv, first, piano=True)
        folder[f"Import A/Full/mvt{i}.musicxml"] = full
        folder[f"Import A/Acc/mvt{i}_strings.musicxml"] = movement("Import A", mv, first, rests=[(1, 4)])
        for name in STRINGS:
            folder[f"Import A/Strings/mvt{i}_{name.replace(' ', '')}.musicxml"] = single_part(full, name)
    folder["__MACOSX/Import A/._x"] = b"junk"
    write_zip(td / "work.zip", folder)
    cfg = {"work": "Import C", "files": ["Strings/mvt1_Cello.musicxml", "Strings/mvt2_Cello.musicxml",
                                         "Strings/mvt1_Violin2.musicxml", "Strings/mvt2_Violin2.musicxml"],
           "parts": ["Cello", "Violin 2"], "practice": "Violin 2"}
    write_zip(td / "with_config.zip", {**folder, "Import A/sheet-practice.json": json.dumps(cfg)})
    (td / "mvt2.musicxml").write_text(movement("Import B", MV_44))
    (td / "mvt10.musicxml").write_text(movement("Import B", MV_38, 13))
    (td / "page.pdf").write_bytes(b"%PDF-1.4")
    odd = movement("Import F", MV_44).replace("<part-name>Flute</part-name>", "<part-name>constructor</part-name>")
    odd = odd.replace("<rights>CC0-1.0</rights>", "").replace("<source>tests/fixtures/make_demo.py</source>", "")
    (td / "odd.musicxml").write_text(odd)

    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    alerts = []
    pg.on("dialog", lambda d: (alerts.append(d.message), d.accept()))
    pg.add_init_script(f"localStorage.setItem('lastWork', '{WORK}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.goto(URL)
    wait_loaded(pg, WORK)
    open_settings_panel(pg)
    check("demo work has no import settings", pg.is_disabled("#btnImportSettings"))

    # A. one ZIP, no questions: the full-score folder, string parts, opened at once
    pick_files(pg, [str(td / "work.zip")])
    note = wait_note(pg, "已匯入")
    check("A: note names the parts and files", "Violin I、Violin II、Viola、Cello、Contrabass" in note and "2 個檔案" in note and "12 個沒用到" in note, note)
    check("A: check is clean", "檢查沒有問題" in note, note)
    try:
        wait_loaded(pg, "Import-A", 20)
        check("A: imported work is open", True)
    except Exception as e:  # noqa: BLE001
        check("A: imported work is open", False, f"{pg.evaluate('[SP.slug, SP.nMeasures]')} {e}")
    w = work_info(pg, "Import-A")
    check("A: five string parts", w and [x["part"] for x in w["parts"]] == ["Violin I", "Violin II", "Viola", "Cello", "Contrabass"])
    pg.click("#importNoteReport")
    pg.wait_for_function("document.getElementById('importDoneDlg').open")
    report = pg.inner_text("#imReport")
    check("A: report lists the files used", "Import A/Full/mvt1.musicxml" in report and "沒有發現問題" in report, report.replace("\n", " | ")[:200])
    pg.click("#importDoneDlg button")

    # B. later: change parts, files, order and names in 匯入設定, then import again
    open_settings_panel(pg)
    check("B: import settings enabled for the imported work", pg.is_enabled("#btnImportSettings"))
    pg.click("#btnImportSettings")
    pg.wait_for_function("document.getElementById('importDlg').open")
    used = pg.eval_on_selector_all("#imFiles li", "ls => ls.filter(l => l.querySelector('input').checked).map(l => l.querySelector('.label').textContent)")
    check("B: dialog shows the files in use", len(used) == 2 and all("/Full/" in u for u in used), str(used))
    checked = pg.eval_on_selector_all("#imParts tbody tr", "rs => rs.filter(r => r.querySelector('input[type=checkbox]').checked).map(r => r.dataset.name)")
    check("B: dialog shows the parts in use", checked == STRINGS, str(checked))
    pg.uncheck('#imParts tr[data-name="Contrabass"] input[type=checkbox]')
    pg.check('#imParts tr[data-name="Piano"] input[type=checkbox]')
    pg.fill('#imParts tr[data-name="Cello"] input[type=text]', "Violoncello")
    pg.click("#imFiles li:nth-child(2) button:has-text('↑')")
    pg.fill("#imWork", "Import A2")
    pg.select_option("#imPractice", "Viola")
    cfg_json = pg.request.get(URL + pg.get_attribute("#imConfig", "href").lstrip("/")).json()
    check("B: settings download as sheet-practice.json", cfg_json.get("work") == "Import A" and len(cfg_json.get("files", [])) == 2, str(cfg_json)[:120])
    held = []
    pg.route("**/api/imports/*/apply?*", lambda route: held.append(route))
    pg.click("#doImport")
    deadline = time.time() + 10
    while not held and time.time() < deadline:
        pg.wait_for_timeout(100)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    check("B: Esc does not close the dialog during the import", pg.evaluate("document.getElementById('importDlg').open"))
    check("B: Cancel is disabled during the import", pg.is_disabled("#importDlg button[value=cancel]"))
    held and held[0].continue_()
    note = wait_note(pg, "已重新匯入")
    pg.unroute("**/api/imports/*/apply?*")
    wait_loaded(pg, "Import-A2", 20)
    w = work_info(pg, "Import-A2")
    check("B: new parts and names", w and [x["part"] for x in w["parts"]] == ["Violin I", "Violin II", "Viola", "Violoncello", "Piano"],
          str(w and [x["part"] for x in w["parts"]]))
    check("B: new movement order (3/8 first)", w and first_beat_type(pg, w["parts"][0]["id"]) == "8")
    check("B: old parts are gone", work_info(pg, "Import-A") is None)
    check("B: practice part from the settings", w and practice_xml_id(pg) == part_xml_id(w, "Viola"), str(practice_xml_id(pg)))

    # B2. again, same work name: the browser's saved practice part follows the new parts
    # an old saved value in this browser: without the update after the re-import, it would win over the server default
    pg.evaluate("localStorage.setItem('sp.settings.Import-A2', JSON.stringify({practice: 'P-old-piece'}))")
    open_settings_panel(pg)
    pg.click("#btnImportSettings")
    pg.wait_for_function("document.getElementById('importDlg').open")
    check("B2: dialog shows the saved practice part", pg.input_value("#imPractice") == "Viola", pg.input_value("#imPractice"))
    pg.fill('#imParts tr[data-name="Viola"] input[type=text]', "Bratsche")
    check("B2: practice follows a renamed part", pg.input_value("#imPractice") == "Bratsche", pg.input_value("#imPractice"))
    pg.select_option("#imPractice", "Violin II")
    pg.click("#doImport")
    note = wait_note(pg, "練習聲部：Violin II")
    pg.wait_for_timeout(500)
    wait_loaded(pg, "Import-A2", 20)
    w = work_info(pg, "Import-A2")
    check("B2: practice part after re-import", w and practice_xml_id(pg) == part_xml_id(w, "Violin II"), str(practice_xml_id(pg)))

    # C. sheet-practice.json in the ZIP sets the import
    pick_files(pg, [str(td / "with_config.zip")])
    note = wait_note(pg, "Import C")
    check("C: config used", "sheet-practice.json" in note and "Cello" in note, note)
    w = work_info(pg, "Import-C")
    check("C: parts from the config", w and [x["part"] for x in w["parts"]] == ["Cello", "Violin II"] and w["parts"][0]["measures"] == 20,
          str(w and [x["part"] for x in w["parts"]]))
    wait_loaded(pg, "Import-C", 20)
    check("C: practice part from the config", w and practice_xml_id(pg) == part_xml_id(w, "Violin II"), f"{practice_xml_id(pg)} vs {w and [(x['part'], x['xmlId']) for x in w['parts']]} slug={pg.evaluate('SP.slug')}")
    check("C: note names the practice part", "練習聲部：Violin II" in note, note)

    # D. several MusicXML files; the same import again gets a new name
    pick_files(pg, [str(td / "mvt10.musicxml"), str(td / "mvt2.musicxml")])
    wait_note(pg, "Import B")
    pick_files(pg, [str(td / "mvt10.musicxml"), str(td / "mvt2.musicxml")])
    note = wait_note(pg, "Import B (2)")
    check("D: second import gets a free name", "已經有「Import B」" in note, note)
    check("D: first work unchanged", len(work_info(pg, "Import-B")["parts"]) == 5)

    # E. PDF mixed with MusicXML is refused before any upload
    pick_files(pg, [str(td / "page.pdf"), str(td / "mvt2.musicxml")])
    pg.wait_for_timeout(300)
    check("E: mixed PDF and MusicXML refused", any("分開匯入" in a for a in alerts), str(alerts))

    # F. a part named "constructor", and no license in the file
    pick_files(pg, [str(td / "odd.musicxml")])
    note = wait_note(pg, "Import F")
    check("F: missing license shown as a warning", "1 個提醒" in note, note)
    wait_loaded(pg, "Import-F", 12)
    open_settings_panel(pg)
    pg.click("#btnImportSettings")
    try:
        pg.wait_for_function("document.getElementById('importDlg').open", timeout=10000)
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    check("F: settings open with a part named constructor", opened and pg.is_visible('#imParts tr[data-name="constructor"]')
          and not pg.is_checked('#imParts tr[data-name="constructor"] input[type=checkbox]'))
    pg.keyboard.press("Escape")

    # G. the bundled example: one click, 4 movements, Violin II as practice part
    open_settings_panel(pg)
    check("G: example button shown", pg.is_visible("#btnExample"))
    pg.click("#btnExample")
    note = wait_note(pg, "Haydn", timeout=180000)
    gslug = next(w["slug"] for w in pg.request.get(URL + "api/works").json() if w["work"].startswith("Haydn"))
    wait_loaded(pg, gslug, 531)
    g = work_info(pg, gslug)
    check("G: four parts", [x["part"] for x in g["parts"]] == ["Violin I", "Violin II", "Viola", "Cello"], str([x["part"] for x in g["parts"]]))
    check("G: four movements in the movement menu", pg.locator("#sectionSelect option").count() == 4, str(pg.locator("#sectionSelect option").count()))
    check("G: practice part Violin II", practice_xml_id(pg) == part_xml_id(g, "Violin II"), note)
    check("G: summary counts movements and bars", "4 個樂章・531 小節" in note, note)
    check("G: alignment line, no suspects", "對齊：沒有發現錯開" in note and pg.locator("#importNoteText .suspect").count() == 0, note)
    pg.click("#importNoteText")
    check("G: a click closes the summary", pg.is_hidden("#importNote"))

    # H. a slow import shows the progress card; the work loads once; the summary closes itself after 15 s
    write_zip(td / "slow.zip", {f"Import H/mvt{i}.musicxml": movement("Import H", mv, first)
                                for i, (mv, first) in enumerate(((MV_44, 1), (MV_38, 13)), start=1)})
    size = (td / "slow.zip").stat().st_size
    cdp = pg.context.new_cdp_session(pg)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1,
                                                  "uploadThroughput": size / 2.5})   # about 2.5 s of upload
    loads = []
    pg.on("request", lambda r: loads.append(r.url) if "/api/works/Import-H/score.musicxml" in r.url else None)
    pg.mouse.move(10, 450)   # not over the summary: hover stops its timer
    pg.evaluate("document.getElementById('importNote').classList.remove('hidden')")   # an old summary is still open
    pick_files(pg, [str(td / "slow.zip")])
    try:
        pg.wait_for_function("!document.getElementById('importProgress').classList.contains('hidden')", timeout=5000)
        shown = True
    except Exception:  # noqa: BLE001
        shown = False
    seen = pg.inner_text("#importProgress .text") if shown else ""
    check("H: progress card shown for a slow import", shown and "slow.zip" in seen, f"{size} bytes, text {seen!r}")
    check("H: the old summary closes when an import starts", pg.is_hidden("#importNote"))
    note = wait_note(pg, "Import H")
    t_note = time.time()
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
    check("H: progress card hidden at the end", pg.is_hidden("#importProgress"))
    check("H: summary lists movements, bars and parts", "2 個樂章・20 小節" in note and "Violin I、Violin II、Viola、Cello、Contrabass" in note, note)
    wait_loaded(pg, "Import-H", 20)
    check("H: imported work loads once", len(loads) == 1, str(loads))
    pg.wait_for_timeout(10000)
    check("H: summary still shown after 10 s", pg.is_visible("#importNote"))
    pg.wait_for_function("document.getElementById('importNote').classList.contains('hidden')", timeout=10000)
    check("H: summary closes itself after about 15 s", 13 < time.time() - t_note < 20, f"{time.time() - t_note:.1f} s")

    # I. the import is saved, but opening the work fails: not reported as a failed import
    (td / "loadfail.musicxml").write_text(movement("Import I", MV_44))
    pg.route("**/api/works", lambda route: route.fulfill(status=500, body="down"))
    pick_files(pg, [str(td / "loadfail.musicxml")])
    note = wait_note(pg, "Import I")
    pg.unroute("**/api/works")
    status = pg.inner_text("#status") if pg.is_visible("#status") else ""
    check("I: summary shown although the work did not open", "已匯入" in note, note)
    check("I: load failure reported, not an import failure", "打開作品失敗" in status and "匯入失敗" not in status, status)
    check("I: the work exists once", work_info(pg, "Import-I") is not None and work_info(pg, "Import-I-2") is None)

    # J. the alignment check found a suspect (put into the server's reply): the summary stays, lists it,
    #    and a click on it goes to that bar; the check report lists it too
    (td / "suspect.musicxml").write_text(movement("Import J", MV_44))
    # Chromium does not give Playwright the body of a multipart upload, so a route cannot pass it on.
    # Change the reply in the page instead: the next import reply gets one suspect.
    pg.evaluate("""() => {
      const text = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "responseText");
      const open = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function (m, u, ...rest) { this.__url = u; return open.call(this, m, u, ...rest); };
      Object.defineProperty(XMLHttpRequest.prototype, "responseText", { configurable: true, get() {
        const t = text.get.call(this);
        if (!/\\/api\\/imports\\?progress=/.test(this.__url || "") || this.status !== 200) return t;
        const r = JSON.parse(t);
        if (window.__alignError) r.alignment = { error: "RuntimeError: test" };
        else r.alignment.suspects = [{ part: "Violin I", from: "1:5", to: "1:8", app_bars: [5, 8], shift: 1,
                                       score: 4, marks_fit_shifted: 4, marks_fit_in_place: 0 }];
        return JSON.stringify(r);
      } });
    }""")
    pick_files(pg, [str(td / "suspect.musicxml")])
    note = wait_note(pg, "Import J")
    wait_loaded(pg, "Import-J", 12)
    suspect = "Violin I：第 5–8 小節，可能早了 1 小節"
    check("J: summary shows the suspect", "對齊：1 處可能錯開" in note and suspect in note, note)
    check("J: summary stays open", "sticky" in pg.get_attribute("#importNote", "class"), pg.get_attribute("#importNote", "class"))
    pg.evaluate("document.getElementById('importNoteReport').click()")
    report = pg.inner_text("#imReport")
    check("J: check report lists the suspect", "各聲部對齊" in report and "? " + suspect in report, report[-400:])
    pg.keyboard.press("Escape")
    pg.evaluate("document.getElementById('importNote').classList.remove('hidden')")
    pg.click("#importNoteText .suspect")
    pg.wait_for_timeout(500)
    bar = pg.evaluate("SP.steps[SP.pos].measure + 1")
    check("J: a click on the suspect goes to its bar", bar == 5 and pg.is_hidden("#importNote"), f"bar {bar}")

    # K. the alignment check failed on the server: the import is shown, but not as checked and clean
    (td / "alignerror.musicxml").write_text(movement("Import K", MV_44))
    pg.evaluate("window.__alignError = true")
    pick_files(pg, [str(td / "alignerror.musicxml")])
    note = wait_note(pg, "Import K")
    wait_loaded(pg, "Import-K", 12)
    check("K: summary says the alignment check failed", "對齊檢查失敗" in note, note)
    check("K: summary stays open", "sticky" in pg.get_attribute("#importNote", "class"), pg.get_attribute("#importNote", "class"))
    pg.evaluate("document.getElementById('importNoteReport').click()")
    title = pg.inner_text("#imDoneTitle")
    check("K: report title is not 'no problems'", "對齊檢查失敗" in title and title != "檢查沒有問題", title)
    pg.keyboard.press("Escape")
    pg.evaluate("window.__alignError = false")

    for slug in ("Import-A2", "Import-C", "Import-B", "Import-B-2", "Import-F", "Import-H", "Import-I", "Import-J", "Import-K", gslug):
        for x in (work_info(pg, slug) or {"parts": []})["parts"]:
            pg.request.delete(f"{URL}api/pieces/{x['id']}")
    left = [w["slug"] for w in pg.request.get(URL + "api/works").json() if w["slug"].startswith("Import")]
    check("cleanup: test works deleted", not left, str(left))
    data_dir = Path(os.environ.get("SP_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").expanduser()
    imports = list((data_dir / "_imports").iterdir()) if (data_dir / "_imports").exists() else []
    check("cleanup: import records removed with their parts", not imports, str([p.name for p in imports]))
    b.close()

print("FAIL: " + ", ".join(fails)) if fails else print("All score import checks passed.")
