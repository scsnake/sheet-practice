from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式
logs=[]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"]); pg = b.new_page(viewport={"width":1500,"height":900}); pg.add_init_script(f"localStorage.setItem('lastWork', '{WORK}'); localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.on("pageerror", lambda e: logs.append(str(e)))
    pg.goto("http://localhost:8777/"); pg.wait_for_load_state("networkidle")
    pg.mouse.click(700, 600)
    pg.wait_for_function("window.SP && SP.slug==='" + WORK + "' && SP.steps.length>1000", timeout=240000); pg.wait_for_timeout(2000)
    pg.keyboard.press("Home"); pg.wait_for_timeout(500)
    pg.evaluate("bpm.value = 50; bpm.dispatchEvent(new Event('input'));"); pg.wait_for_timeout(300)
    print("mvt1:", pg.evaluate("JSON.stringify({slider: bpm.value, label: bpmVal.textContent, tempoNow: tempoNow.textContent, speeds: SP.speeds})"))
    pg.select_option("#sectionSelect", "2"); pg.mouse.click(700, 600); pg.wait_for_timeout(1500)
    print("mvt3 before:", pg.evaluate("JSON.stringify({slider: bpm.value, tempoNow: tempoNow.textContent})"))
    pg.evaluate("bpm.value = 100; bpm.dispatchEvent(new Event('input'));"); pg.wait_for_timeout(300)
    print("mvt3 after:", pg.evaluate("JSON.stringify({slider: bpm.value, tempoNow: tempoNow.textContent, speeds: SP.speeds, secPerBar: +(SP.measureStartSec[361]-SP.measureStartSec[360]).toFixed(3)})"))
    pg.select_option("#sectionSelect", "0"); pg.mouse.click(700, 600); pg.wait_for_timeout(1500)
    print("back to mvt1:", pg.evaluate("JSON.stringify({slider: bpm.value, tempoNow: tempoNow.textContent, secPerBar: +(SP.measureStartSec[1]-SP.measureStartSec[0]).toFixed(3)})"))
    # playback at mvt3 100%: 3/8 at 116 -> 1.5 beats per bar -> 0.776 s/bar; 3 s ≈ 3.9 bars
    pg.select_option("#sectionSelect", "2"); pg.mouse.click(700, 600); pg.wait_for_timeout(1500)
    pg.evaluate("countIn.checked=false; countIn.dispatchEvent(new Event('change')); SP.loopOn=false;")
    pg.keyboard.press("Space"); pg.wait_for_timeout(3000); print("mvt3 after 3s play: bar", pg.evaluate("SP.steps[SP.pos].measure+1")); pg.keyboard.press("Space")
    pg.wait_for_timeout(1200); print("saved:", pg.evaluate("(j => JSON.stringify({speeds:j.speeds, speed:j.speed}))(JSON.parse(localStorage.getItem('sp.settings." + WORK + "')))"))
    b.close()
print(logs[:3])
