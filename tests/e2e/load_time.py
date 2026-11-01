import time, json
from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式

URL = "http://localhost:8777/"
OUT = str(__import__('pathlib').Path(__file__).parent / 'out')


def open_work(ctx, slug, label):
    pg = ctx.new_page()
    pg.add_init_script(f"localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(f"localStorage.setItem('lastWork', '{slug}')")
    t0 = time.time()
    pg.goto(URL)
    pg.wait_for_function("document.querySelector('#score .view.active svg, #score.scroll .view:not(.rendering) svg')", timeout=240000)
    t_first = time.time() - t0
    pg.wait_for_function("window.SP && SP.ready", timeout=240000)
    t_ready = time.time() - t0
    info = pg.evaluate("JSON.stringify({win: SP.win, steps: SP.steps.length, n: SP.nMeasures, parts: SP.parts.length, errors: SP.errors.length, tempo0: SP.tempo[0]})")
    print(f"{label:28s} first render {t_first:5.2f}s  ready {t_ready:5.2f}s  {info}  errors={errs[:2]}")
    return pg


with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1500, "height": 900})  # 同一個 context：第二次開會用到 IndexedDB 快取
    for slug in [WORK, SOLO_WORK]:
        open_work(ctx, slug, f"{slug} cold").close()
        pg = open_work(ctx, slug, f"{slug} warm")
        if slug == WORK:
            # 翻頁：從接近頁尾的地方開始播，記錄游標進入下一頁第一小節與畫面切換的時間，以及每一幀的間隔
            pg.evaluate("SP.speeds = [1, 1, 1, 1]; SP.speed = 1; SP.skipSolo = false; SP.loopOn = false; countIn.checked = false; countIn.dispatchEvent(new Event('change'));")
            pg.fill("#gotoBar", "1:45"); pg.keyboard.press("Enter"); pg.wait_for_timeout(5000)
            pool = "JSON.stringify([...SP.pool.values()].map(v => [v.from + 1, v.to + 1, v.div.classList.contains('rendering') ? 'rendering' : 'ready']))"
            print("  start bar", pg.evaluate("SP.steps[SP.pos].measure + 1"), "page", pg.evaluate("JSON.stringify({from: SP.win.from + 1, to: SP.win.to + 1})"), "pool before play", pg.evaluate(pool))
            boundary = pg.evaluate("SP.win.to + 1")
            pg.evaluate("""() => { window.__rec = []; const t0 = performance.now(); const tick = () => { window.__rec.push([+(performance.now()-t0).toFixed(1), SP.win.from, SP.steps[SP.pos].measure]); if (window.__rec.length < 4000) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }""")
            pg.keyboard.press("Space")
            pg.wait_for_timeout(14000)
            pg.keyboard.press("Space")
            rec = pg.evaluate("window.__rec")
            gaps = [(round(rec[i][0] - rec[i - 1][0], 1), rec[i][2] + 1) for i in range(1, len(rec))]
            enter = next((r for r in rec if r[2] >= boundary), None)
            switched = next((r for r in rec if r[1] >= boundary), None)
            print("  next page starts at bar", boundary + 1, "| cursor entered it at", enter and enter[0], "ms | page shown at", switched and switched[0], "ms")
            print("  frames:", len(rec), "| gaps >50ms (ms, bar):", [g for g in gaps if g[0] > 50][:10], "| max gap", max(g[0] for g in gaps), "ms")
            print("  pool after", pg.evaluate(pool))
            pg.screenshot(path=f"{OUT}/perf_flip.png")
        pg.close()
    b.close()
