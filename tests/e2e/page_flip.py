from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import os
DISPLAY = os.environ.get("SP_DISPLAY", "page")   # 預設用翻頁模式跑；SP_DISPLAY=scroll 改用捲動模式
URL = "http://localhost:8777/"
HOOK = """(() => {
  window.__late = []; window.__osc = 0;
  const start = OscillatorNode.prototype.start;
  OscillatorNode.prototype.start = function (when) {
    const c = this.context;
    if (typeof when === 'number' && c && c.state === 'running') {
      window.__osc++;
      const late = c.currentTime - when;
      if (late > 0.02) window.__late.push([Math.round(performance.now()), +late.toFixed(3)]);
    }
    return start.apply(this, arguments);
  };
})();"""
REC = """() => { const token = (window.__tok = (window.__tok || 0) + 1); window.__rec = []; const t0 = performance.now(); window.__t0 = t0; window.__late = []; window.__osc = 0;
  const tick = () => { if (window.__tok !== token) return; window.__rec.push([+(performance.now() - t0).toFixed(1), SP.win.from, SP.steps[SP.pos].measure, 0, 0]); if (window.__rec.length < 5000) requestAnimationFrame(tick); };
  requestAnimationFrame(tick); }"""
STOPREC = "() => { window.__tok = (window.__tok || 0) + 1; }"
POOL = "JSON.stringify([...SP.pool.values()].map(v => (v.from + 1) + '-' + (v.to + 1) + (v.div.classList.contains('rendering') ? '(rendering)' : '')))"

def scenario(pg, label, drop_next):
    pg.fill("#gotoBar", "1:47"); pg.keyboard.press("Enter")
    pg.evaluate("bpm.value = 100; bpm.dispatchEvent(new Event('input'));")
    pg.wait_for_timeout(6000)
    boundary = pg.evaluate("SP.win.to + 1")
    if drop_next:
        pg.evaluate(f"() => {{ const v = SP.pool.get({boundary}); if (v) {{ try {{ v.osmd.clear(); }} catch (e) {{}} v.div.remove(); SP.pool.delete({boundary}); }} }}")
    pool = pg.evaluate(POOL)
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.evaluate(REC)
    pg.keyboard.press("Space")
    pg.wait_for_timeout(12000)
    pg.keyboard.press("Space")
    pg.evaluate(STOPREC)
    rec = pg.evaluate("window.__rec")
    i_big = max(range(1, len(rec)), key=lambda i: rec[i][0] - rec[i - 1][0])
    before, after = rec[i_big - 1], rec[i_big]
    enter = next((r for r in rec if r[2] >= boundary), None)
    shown = next((r for r in rec if r[1] >= boundary), None)
    flip_gaps = [round(rec[i][0] - rec[i - 1][0], 1) for i in range(1, len(rec)) if enter and abs(rec[i][0] - enter[0]) < 1500]
    print(f"[{label}] pool at play: {pool}")
    print(f"   page {boundary + 1} starts: cursor entered at {enter and enter[0]} ms, page shown at {shown and shown[0]} ms (delay {shown and enter and round(shown[0] - enter[0], 1)} ms), last bar {rec[-1][2] + 1}")
    print(f"   frames {len(rec)}; worst frame gap {round(after[0] - before[0], 1)} ms at bar {after[2] + 1}; worst gap within ±1.5 s of the flip {max(flip_gaps) if flip_gaps else None} ms")
    late = pg.evaluate("window.__late.map(([t, l]) => [+(t - window.__t0).toFixed(0), l])"); osc = pg.evaluate("window.__osc")
    near = [l for t, l in late if enter and abs(t - enter[0]) < 2000]
    after_start = [l for t, l in late if t > 1000]
    print(f"   notes started: {osc}; started >20 ms late: {len(late)} (after the first second: {len(after_start)}, max {max(after_start) if after_start else 0} s); late within ±2 s of the flip: {len(near)} (max {max(near) if near else 0} s)")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1500, "height": 900})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.add_init_script(HOOK)
    pg.add_init_script(f"localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.add_init_script("localStorage.setItem('lastWork', '" + WORK + "')")
    pg.goto(URL); pg.wait_for_function("window.SP && SP.ready && SP.slug === '" + WORK + "'", timeout=120000)
    pg.evaluate("countIn.checked = false; countIn.dispatchEvent(new Event('change')); if (loopOn.checked) { loopOn.checked = false; loopOn.dispatchEvent(new Event('change')); } if (skipSolo.checked) { skipSolo.checked = false; skipSolo.dispatchEvent(new Event('change')); } soundMode.value = 'pure'; soundMode.dispatchEvent(new Event('change'));")
    pg.wait_for_timeout(3000)
    scenario(pg, "A next page prerendered", False)
    scenario(pg, "B next page NOT prerendered", True)
    print("errors:", errs)
    b.close()
