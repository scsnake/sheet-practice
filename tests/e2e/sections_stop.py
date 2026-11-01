from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式
OUT = str(__import__('pathlib').Path(__file__).parent / 'out')
logs=[]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"]); pg = b.new_page(viewport={"width":1500,"height":1000}); pg.add_init_script(f"localStorage.setItem('lastWork', '{WORK}'); localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.on("pageerror", lambda e: logs.append(str(e)))
    pg.goto("http://localhost:8777/"); pg.wait_for_load_state("networkidle")
    pg.mouse.click(700, 600)
    pg.wait_for_function("window.SP && SP.slug==='" + WORK + "' && SP.steps.length>1000", timeout=240000); pg.wait_for_timeout(2500)
    print(pg.evaluate("JSON.stringify({sections: SP.sections, win: SP.win, label: winLabel.textContent, tempoNow: tempoNow.textContent, tuning: SP.tuning, opts: sectionSelect.options.length})"))
    pg.select_option("#sectionSelect", "2"); pg.wait_for_timeout(1500)
    print("mvt3:", pg.evaluate("JSON.stringify({m: SP.steps[SP.pos].measure, win: SP.win, label: winLabel.textContent})"))
    pg.mouse.click(700, 600); pg.keyboard.press("PageUp"); pg.wait_for_timeout(1200)
    print("PageUp from mvt3 start:", pg.evaluate("JSON.stringify({win: SP.win, label: winLabel.textContent})"))
    pg.keyboard.press("PageDown"); pg.wait_for_timeout(1200); pg.keyboard.press("PageDown"); pg.wait_for_timeout(1200)
    print("PageDown x2:", pg.evaluate("JSON.stringify({win: SP.win, label: winLabel.textContent})"))
    # stop keeps position
    pg.evaluate("countIn.checked=false; countIn.dispatchEvent(new Event('change'));")
    pg.keyboard.press("Space"); pg.wait_for_timeout(2500); before = pg.evaluate("SP.steps[SP.pos].measure")
    pg.click("#btnStop"); pg.wait_for_timeout(300)
    print("stop keeps position:", before, pg.evaluate("SP.steps[SP.pos].measure"), "playing", pg.evaluate("SP.playing"))
    pg.screenshot(path=f"{OUT}/s6_sections.png")
    b.close()
print(logs[:3])
