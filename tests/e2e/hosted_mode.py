"""The hosted app (build_static.py): a static site with no server. Works live in the browser's IndexedDB.

Builds the site in a temporary folder and serves it with a plain static file server on port 8779
(not the app server). Uses the CC0 Haydn example only.

  uv run --group dev python tests/e2e/hosted_mode.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
import base64, io, json, socket, subprocess, sys, tempfile, time, zipfile

ROOT = Path(__file__).resolve().parents[2]
PORT = 8779
URL = f"http://127.0.0.1:{PORT}/"
EXAMPLE = "haydn-op76-no3"

fails = []
def check(name, ok, detail=""):
    print(("OK  " if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

def drag(pg, kind, files=(), target="#main"):
    return pg.evaluate("""([kind, files, sel]) => {
      const dt = new DataTransfer();
      for (const [name, b64] of files) dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name));
      const ev = new DragEvent(kind, { dataTransfer: dt, bubbles: true, cancelable: true });
      document.querySelector(sel).dispatchEvent(ev);
      return ev.defaultPrevented;
    }""", [kind, [[n, base64.b64encode(b).decode()] for n, b in files], target])

def drop_files(pg, files):
    drag(pg, "dragenter", files)
    drag(pg, "dragover", files, target="#dropOverlay")
    return drag(pg, "drop", files, target="#dropOverlay")

def wait_note(pg, text, timeout=120000):
    pg.wait_for_function(f"!document.getElementById('importNote').classList.contains('hidden') && "
                         f"document.getElementById('importNoteText').textContent.includes({json.dumps(text)})", timeout=timeout)
    return pg.inner_text("#importNoteText")

def wait_ready(pg, slug=None, timeout=120000):
    cond = "window.SP && SP.ready && SP.slug && SP.loadTimes && SP.loadTimes.source"
    if slug: cond += f" && SP.slug === {json.dumps(slug)}"
    pg.wait_for_function(cond, timeout=timeout)

def wait_status(pg, text, timeout=30000):
    pg.wait_for_function(f"document.getElementById('status').textContent.includes({json.dumps(text)})", timeout=timeout)
    return pg.inner_text("#status")

def renamed(package: bytes, slug: str, work: str) -> bytes:
    """The same ready package as another work (a second work for the tests)."""
    src, out = zipfile.ZipFile(io.BytesIO(package)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "sheet-practice-ready.json":
                m = json.loads(data)
                m["slug"], m["work"], m["summary"]["work"] = slug, work, work
                data = json.dumps(m, ensure_ascii=False).encode()
            z.writestr(info.filename, data)
    return out.getvalue()

def click_first_note(pg, measure=4):
    pos = pg.evaluate("""(m) => { const staff = SP.parts[1].staves[0]; const gm = SP.osmd.GraphicSheet.MeasureList[m][staff];
      for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) {
        if (gn.sourceNote.isRest()) continue;
        const ap = gn.PositionAndShape.AbsolutePosition, r = document.getElementById('score').getBoundingClientRect();
        return {x: r.left + ap.x * 10 * SP.zoom, y: r.top + ap.y * 10 * SP.zoom}; } return null; }""", measure)
    pg.mouse.click(pos["x"], pos["y"])
    pg.wait_for_timeout(300)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    site = td / "site"
    out = subprocess.run([sys.executable, str(ROOT / "build_static.py"), "-o", str(site)], capture_output=True, text=True, cwd=ROOT)
    check("build_static.py builds the site", out.returncode == 0 and (site / "index.html").is_file(), out.stdout + out.stderr[-500:])
    package = (site / "examples" / f"{EXAMPLE}.zip").read_bytes()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for f in (ROOT / "examples" / EXAMPLE).iterdir():
            if f.suffix in (".mxl", ".json"):
                z.write(f, f.name)
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", str(site)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", PORT), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            ctx.add_init_script("localStorage.setItem('displayMode', 'page'); localStorage.setItem('toolbarAutoHide', 'off')")
            pg = ctx.new_page()
            api_requests, errors, dialogs = [], [], []
            pg.on("request", lambda r: "/api/" in r.url and api_requests.append(r.url))
            pg.on("pageerror", lambda e: errors.append(str(e)))
            answer = {"value": True}
            def on_dialog(d):
                dialogs.append(d.message)
                d.accept() if answer["value"] else d.dismiss()
            pg.on("dialog", on_dialog)

            # A. empty site: hosted texts, server-only buttons hidden, the example is offered
            pg.goto(URL)
            pg.wait_for_selector("#emptyState:not(.hidden)")
            pg.wait_for_selector("#emptyExamples:not(.hidden) .exampleButtons button")
            check("A empty state explains packages", "樂譜包" in pg.inner_text("#emptyHint"))
            check("A server-only controls hidden", pg.evaluate("""['btnImportSettings', 'importNoteSettings'].every((id) => document.getElementById(id).classList.contains('hidden'))
                && document.getElementById('showPages').closest('label').classList.contains('hidden')"""))
            check("A file picker takes only ZIP", pg.get_attribute("#importInput", "accept") == ".zip")

            # B. import the example: the summary comes from the package (with the alignment check), the timeline too
            pg.click("#emptyExamples .exampleButtons button")
            note = wait_note(pg, "對齊")
            wait_ready(pg)
            slug = pg.evaluate("SP.slug")
            check("B example imported and opened", pg.evaluate("SP.parts.length") == 4 and pg.evaluate("SP.nMeasures") == 531, slug)
            check("B summary with check and alignment", "檢查沒有問題" in note and "對齊：沒有發現錯開" in note, note.replace("\n", " | "))
            check("B timeline from the package", pg.evaluate("SP.loadTimes.source") == "server")
            check("B practice part from the package", pg.evaluate("SP.parts[SP.practice].name") == "Violin II")
            original = pg.evaluate("SP.xmlText")

            # C. reload: the work is still there, read from IndexedDB
            pg.reload()
            wait_ready(pg, slug)
            check("C work kept after reload", pg.evaluate("SP.xmlText") == original and pg.evaluate("SP.loadTimes.source") == "server")

            # D. files that are not ready packages
            check("D drop is handled by the page", drop_files(pg, [("score.zip", raw.getvalue())]))
            text = wait_status(pg, "還沒有做成樂譜包")
            check("D raw score ZIP refused with a hint", "check_import.py --export" in text, text)
            drop_files(pg, [("part.pdf", b"%PDF-1.4")])
            text = wait_status(pg, "不支援的檔案")
            check("D PDF refused", "樂譜包" in text, text)
            drop_files(pg, [("a.zip", package), ("b.zip", package)])
            text = wait_status(pg, "一次匯入一個")
            check("D two ZIPs refused", True, text)
            drop_files(pg, [("broken.zip", b"PK not a zip")])
            check("D broken ZIP refused", "不是 ZIP" in wait_status(pg, "broken.zip"))

            # a ZIP bomb: the size in the ZIP is false, the entry expands past the limit (64 MB)
            bomb = io.BytesIO()
            with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("sheet-practice-ready.json", b"\0" * (80 << 20))
            bomb = bytearray(bomb.getvalue())
            cd = bomb.rindex(b"PK\x01\x02")
            bomb[cd + 24:cd + 28] = (100).to_bytes(4, "little")
            drop_files(pg, [("bomb.zip", bytes(bomb))])
            text = wait_status(pg, "太大", timeout=60000)
            check("D ZIP bomb stopped at the limit", "64 MB" in text and "JSON" not in text, text)

            # E. drop a second package (a Unicode slug, as works.slug() makes for a Chinese name)
            drop_files(pg, [("second.zip", renamed(package, "第二首-Second-Work", "Second Work"))])
            note = wait_note(pg, "Second Work")
            wait_ready(pg, "第二首-Second-Work")
            names = pg.evaluate("[...document.querySelectorAll('#workSelect option')].map((o) => o.value)")
            check("E second package imported", sorted(names) == sorted([slug, "第二首-Second-Work"]), str(names))

            # F. edit a note: the edited merged score is saved in IndexedDB as it is
            pg.evaluate("(s) => { const el = document.getElementById('workSelect'); el.value = s; el.dispatchEvent(new Event('change')); }", slug)
            wait_ready(pg, slug)
            pg.evaluate("editMode.checked = true; editMode.dispatchEvent(new Event('change'));")
            click_first_note(pg)
            check("F note selected", pg.evaluate("!!SP.selected"), pg.inner_text("#editStatus"))
            pg.keyboard.press("ArrowUp")
            pg.wait_for_function("document.getElementById('editStatus').textContent.includes('已儲存')", timeout=60000)
            edited = pg.evaluate("SP.xmlText")
            check("F score changed", edited != original)
            pg.evaluate("editMode.checked = false; editMode.dispatchEvent(new Event('change'));")
            pg.reload()
            wait_ready(pg, slug)
            check("F edit kept after reload", pg.evaluate("SP.xmlText") == edited)
            check("F timeline analyzed in the browser after the edit", pg.evaluate("SP.loadTimes.source") == "analyze")
            pg.wait_for_timeout(1500)   # the analyzed timeline is stored after the load
            pg.reload()
            wait_ready(pg, slug)
            check("F analyzed timeline stored", pg.evaluate("SP.loadTimes.source") == "server" and pg.evaluate("SP.xmlText") == edited)

            # G. download: the current score, with the edit
            pg.evaluate("document.querySelector('[data-pop=settings] [data-poptoggle]').click()")
            with pg.expect_download() as dl:
                pg.click("#downloadXml")
            data = Path(dl.value.path()).read_text(encoding="utf-8")
            check("G download is the edited score", data == edited and dl.value.suggested_filename == f"{slug}.musicxml", dl.value.suggested_filename)
            pg.keyboard.press("Escape")

            # H. import the same package again: asks first, because the work has edits
            answer["value"] = False
            drop_files(pg, [(f"{EXAMPLE}.zip", package)])
            text = wait_status(pg, "沒有匯入")
            check("H keep the edits when the user says no", pg.evaluate("SP.xmlText") == edited and "改過音符" in dialogs[-1], text)
            answer["value"] = True
            drop_files(pg, [(f"{EXAMPLE}.zip", package)])
            wait_note(pg, "已重新匯入")
            wait_ready(pg, slug)
            check("H replaced by the package when the user says yes", pg.evaluate("SP.xmlText") == original)

            # I. the works dialog: list and delete
            pg.evaluate("document.getElementById('btnManage').click()")
            pg.wait_for_selector("#worksDlg[open]")
            rows = pg.evaluate("[...document.querySelectorAll('#worksTable tbody tr')].map((r) => r.cells[0].textContent)")
            check("I works dialog lists both works", len(rows) == 2, str(rows))
            pg.evaluate("[...document.querySelectorAll('#worksTable tbody tr')].find((r) => r.cells[0].textContent === 'Second Work').querySelector('button').click()")
            pg.wait_for_function("document.querySelectorAll('#worksTable tbody tr').length === 1")
            pg.wait_for_function("document.querySelectorAll('#workSelect option').length === 1")
            check("I second work deleted", pg.evaluate("document.getElementById('workSelect').value") == slug)

            # J. play a little
            pg.keyboard.press("Escape")
            pg.click("#btnPlay")
            pg.wait_for_timeout(1500)
            check("J plays", pg.evaluate("SP.playing"))
            pg.click("#btnStop")

            check("K no request reached a server API", not api_requests, str(api_requests[:3]))
            check("K no page errors", not errors, "; ".join(errors)[:500])
            browser.close()
    finally:
        server.terminate()
        server.wait()

print("FAIL" if fails else "All hosted-mode checks passed.")
sys.exit(1 if fails else 0)
