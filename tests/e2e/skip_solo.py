from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"]); pg = b.new_page(viewport={"width": 1500, "height": 900}); pg.add_init_script(f"localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.add_init_script("localStorage.setItem('lastWork', '" + SOLO_WORK + "')")
    pg.goto("http://localhost:8777/"); pg.wait_for_function("window.SP && SP.ready && SP.slug === '" + SOLO_WORK + "'", timeout=120000); pg.wait_for_timeout(2000)
    pg.evaluate("countIn.checked = false; countIn.dispatchEvent(new Event('change')); if (loopOn.checked) { loopOn.checked = false; loopOn.dispatchEvent(new Event('change')); } skipSolo.checked = true; skipSolo.dispatchEvent(new Event('change'));")
    print("skips:", pg.evaluate("JSON.stringify(SP.skips.slice(0, 3).map(k => [k.start + 1, k.end + 1]))"))
    pg.fill("#gotoBar", "#1"); pg.keyboard.press("Enter"); pg.evaluate("bpm.value = 150; bpm.dispatchEvent(new Event('input'));"); pg.wait_for_timeout(1500)
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Space"); pg.wait_for_timeout(800)
    print("play from bar 1 (inside skip) starts at bar", pg.evaluate("SP.steps[SP.pos].measure + 1")); pg.keyboard.press("Space")
    pg.fill("#gotoBar", "#119"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Space"); seen = []
    for i in range(30):
        pg.wait_for_timeout(400); m = pg.evaluate("SP.steps[SP.pos].measure + 1")
        if not seen or seen[-1] != m: seen.append(m)
    pg.keyboard.press("Space")
    print("speed", pg.evaluate("bpmVal.textContent"), "| bars visited from 119:", seen)
    print("errors:", errs)
    b.close()
