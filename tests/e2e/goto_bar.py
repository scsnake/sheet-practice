from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page(viewport={"width":1500,"height":900}); pg.add_init_script(f"localStorage.setItem('lastWork', '{WORK}'); localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.goto("http://localhost:8777/"); pg.wait_for_load_state("networkidle")
    pg.mouse.click(700, 600)
    pg.wait_for_function("window.SP && SP.slug==='" + WORK + "' && SP.steps.length>1000", timeout=240000); pg.wait_for_timeout(2000)
    for txt in ["3:12", "50", "#500", "1:1", "999"]:
        pg.fill("#gotoBar", txt); pg.keyboard.press("Enter"); pg.wait_for_timeout(1200)
        print(txt, "->", pg.evaluate("JSON.stringify({global: SP.steps[SP.pos].measure+1, label: tempoNow.textContent.split('　')[0]})"))
    b.close()
