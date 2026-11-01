import json
from playwright.sync_api import sync_playwright
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式

OUT = str(__import__('pathlib').Path(__file__).parent / 'out')
logs = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    page.add_init_script(f"localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    page.on("pageerror", lambda e: logs.append(f"[pageerror] {e}"))
    page.on("dialog", lambda d: d.accept("2") if d.type == "prompt" else d.accept())
    page.goto("http://localhost:8777/")
    page.wait_for_function("window.SP && window.SP.steps.length > 0", timeout=120000)
    print("errors", page.evaluate("JSON.stringify({total: SP.errors.length, byPart: SP.parts.map((p,i)=>SP.errors.filter(e=>e.part===i && !e.empty).length), measures: SP.measureFirstStep.length})"))
    page.evaluate("SP.pos = 0; SP.loop = {start: 2, end: 5}; SP.loopOn = true;")
    page.keyboard.press("Home"); page.wait_for_timeout(500)
    page.evaluate("SP.clickRanges=[[2,3]]; clickMode.value='ranges'; clickMode.dispatchEvent(new Event('change'));")
    page.screenshot(path=f"{OUT}/u1_quartet.png")
    # insert 2 measures after Violin II measure 3 (index 2), then delete one
    page.evaluate("editMode.checked=true; editMode.dispatchEvent(new Event('change'));")
    nb = page.evaluate("""() => { const staff = SP.parts[0].staves[0]; const gm=SP.osmd.GraphicSheet.MeasureList[2][staff]; for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) { const ap=gn.PositionAndShape.AbsolutePosition; const r=document.getElementById('score').getBoundingClientRect(); return {x:r.left+ap.x*10*SP.zoom, y:r.top+ap.y*10*SP.zoom}; } }""")
    page.mouse.click(nb["x"], nb["y"]); page.wait_for_timeout(200)
    print("selected", page.evaluate("JSON.stringify(SP.selected)"))
    before = page.evaluate("SP.xmlDoc.querySelectorAll('score-partwise > part')[0].querySelectorAll(':scope > measure').length")
    page.keyboard.press("i"); page.wait_for_timeout(5000)
    after = page.evaluate("SP.xmlDoc.querySelectorAll('score-partwise > part')[0].querySelectorAll(':scope > measure').length")
    print("vn2 measures before/after insert:", before, after, "total measures", page.evaluate("SP.measureFirstStep.length"), page.evaluate("editStatus.textContent"))
    page.screenshot(path=f"{OUT}/u2_inserted.png")
    # select the inserted measure rest (measure index 3) and delete it
    nb = page.evaluate("""() => { const staff = SP.parts[0].staves[0]; const gm=SP.osmd.GraphicSheet.MeasureList[3][staff]; for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) { const ap=gn.PositionAndShape.AbsolutePosition; const r=document.getElementById('score').getBoundingClientRect(); return {x:r.left+ap.x*10*SP.zoom, y:r.top+ap.y*10*SP.zoom, rest: gn.sourceNote.isRest()}; } }""")
    print("inserted measure note", nb)
    page.mouse.click(nb["x"], nb["y"]); page.wait_for_timeout(200)
    page.keyboard.press("x"); page.wait_for_timeout(5000)
    print("after delete:", page.evaluate("SP.xmlDoc.querySelectorAll('score-partwise > part')[0].querySelectorAll(':scope > measure').length"))
    page.keyboard.press("Meta+z"); page.wait_for_timeout(4000)
    page.keyboard.press("Meta+z"); page.wait_for_timeout(4000)
    print("after undo x2:", page.evaluate("SP.xmlDoc.querySelectorAll('score-partwise > part')[0].querySelectorAll(':scope > measure').length"))
    # 存檔是延遲 800 ms 的，關掉瀏覽器前要等它寫回去，否則會把中間狀態留在使用者的譜裡
    page.wait_for_timeout(2500)
    saved = page.evaluate("fetch('/api/works/' + SP.slug + '/score.musicxml').then(r => r.text()).then(x => (x.match(/<measure[ >]/g) || []).length / SP.parts.length)")
    print("saved on the server:", saved, "bars per part")
    browser.close()
print("\n".join(logs)[:1500])
