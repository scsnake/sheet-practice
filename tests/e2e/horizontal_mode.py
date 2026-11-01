"""橫向模式（顯示：橫向）的四種跟隨方式比較。

樂譜排成一整行（OSMD renderSingleHorizontalStaffline），時間往右走，不需要上下捲動。
播放時樂譜怎麼跟著游標往左移，有四種做法（設定面板的「橫向跟隨」，或 localStorage.hFollow）：

  step   目前小節釘在畫面左邊三分之一，換小節時滑過去
  smooth 游標固定在同一個 x，樂譜連續往左流
  jump   小節走到畫面最右邊才跳，讓它變成最左邊的小節
  half   游標過了畫面三分之二就往左移半個畫面

每種方式播 SP_PLAY_SEC 秒（預設 12），記錄：
  - 前面看得到幾小節（lookahead）：最少幾小節、平均幾小節 ← 看得夠遠才跟得上
  - 游標在畫面上的 x 範圍：越小表示閱讀位置越固定
  - 每幀移動多少、有幾幀在動：越少動越好讀，但動的時候不能太大
  - 最長幀間隔、音符遲到數、DOM 裡的塊數（追加樂譜不能吃掉音訊）

會暫時改速度、關掉預備拍／循環，結束時還原。
"""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK

URL = "http://localhost:8777/"
OUT = Path(__file__).parent / "out"
PLAY_SEC = float(os.environ.get("SP_PLAY_SEC", "12"))
POLICIES = os.environ.get("SP_POLICIES", "step,smooth,jump,half").split(",")
SLUG = WORK
GOTO = os.environ.get("SP_GOTO", "3:1")     # 第 3 樂章 3/8 拍，150% 時約 0.5 秒一小節
CHUNK_LIMIT = 6

HOOK = """(() => { window.__late = []; const s = OscillatorNode.prototype.start;
  OscillatorNode.prototype.start = function (w) { const c = this.context;
    if (typeof w === 'number' && c && c.state === 'running' && c.currentTime - w > 0.02) window.__late.push(+(c.currentTime - w).toFixed(3));
    return s.apply(this, arguments); }; })();"""

# 每次重錄換一個 token，上一輪的 requestAnimationFrame 迴圈會自己停（否則資料會混在一起）
REC = """() => { const tok = (window.__tok = (window.__tok || 0) + 1); window.__r = []; window.__late = []; const t0 = performance.now();
  const tick = () => { if (window.__tok !== tok) return;
    const c = SP.cursorView && SP.cursorView.osmd.cursor; const r = c && !c.hidden ? c.cursorElement.getBoundingClientRect() : null;
    window.__r.push([+(performance.now() - t0).toFixed(1), Math.round(scoreWrap.scrollLeft), r ? Math.round(r.left) : null,
                     SP.steps[SP.pos].measure + 1, SP.chunks.length]);
    if (window.__r.length < 8000) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }"""
STOP = "() => { window.__tok = (window.__tok || 0) + 1; }"
GEOM = """(() => { const m = SP.steps[SP.pos].measure, v = SP.chunks.find(c => m >= c.from && m <= c.to) || SP.chunks[0];
  return { pxbar: Math.round(v.div.offsetWidth / (v.to - v.from + 1)), w: scoreWrap.clientWidth, h: scoreWrap.clientHeight,
           vscroll: scoreWrap.scrollHeight > scoreWrap.clientHeight + 2, chunkH: v.div.offsetHeight }; })()"""


def measure(pg, policy, width):
    pg.evaluate(STOP)
    pg.evaluate(f"hFollow.value = '{policy}'; hFollow.dispatchEvent(new Event('change'))")
    pg.fill("#gotoBar", GOTO); pg.keyboard.press("Enter"); pg.wait_for_timeout(800)
    pg.evaluate("bpm.value = 150; bpm.dispatchEvent(new Event('input'));")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.wait_for_timeout(2500)          # 跳小節後先讓畫面安定
    g = pg.evaluate(GEOM)
    pg.evaluate(REC)
    pg.keyboard.press("Space"); pg.wait_for_timeout(PLAY_SEC * 1000); pg.keyboard.press("Space"); pg.evaluate(STOP)
    rec, late = pg.evaluate("window.__r"), pg.evaluate("window.__late")
    pg.screenshot(path=str(OUT / f"horiz_{policy}.png"), clip={"x": 0, "y": 0, "width": width, "height": 520})

    gaps = [rec[i][0] - rec[i - 1][0] for i in range(1, len(rec))]
    moves = [abs(rec[i][1] - rec[i - 1][1]) for i in range(1, len(rec))]
    cur = [r[2] for r in rec if r[2] is not None]
    ahead = [(g["w"] - r[2]) / g["pxbar"] for r in rec if r[2] is not None]
    off = sum(1 for r in rec if r[2] is None or r[2] < 0 or r[2] > g["w"])
    res = {
        "policy": policy, "bars": f"{rec[0][3]}→{rec[-1][3]}", "pxbar": g["pxbar"],
        "barsPerScreen": round(g["w"] / g["pxbar"], 1), "chunkH": g["chunkH"], "needsVScroll": g["vscroll"],
        "cursorRange": max(cur) - min(cur), "aheadMin": round(min(ahead), 1), "aheadAvg": round(sum(ahead) / len(ahead), 1),
        "movingFrames": f"{sum(1 for m in moves if m > 0)}/{len(moves)}", "maxMove": max(moves),
        "maxGap": round(max(gaps)), "chunksMax": max(r[4] for r in rec), "late": len(late), "offScreen": off,
    }
    ok = {
        "no late notes": not late,
        "chunks<=6": res["chunksMax"] <= CHUNK_LIMIT,
        "cursor on screen": off == 0,
        "frame gap<250ms": res["maxGap"] < 250,
        "no vertical scrolling": not g["vscroll"],
    }
    print(f"[{policy:6}] bars {res['bars']}  {res['pxbar']} px/bar ({res['barsPerScreen']} bars/screen, line {res['chunkH']} px tall)")
    print(f"         lookahead {res['aheadMin']}–{round(max(ahead), 1)} bars (avg {res['aheadAvg']}) | cursor x range {res['cursorRange']} px")
    print(f"         moved {res['movingFrames']} frames, max {res['maxMove']} px/frame | max gap {res['maxGap']} ms | chunks {res['chunksMax']} | late {res['late']}")
    print(f"         {'PASS' if all(ok.values()) else 'FAIL'}: " + ", ".join(f"{k}={'ok' if v else 'NO'}" for k, v in ok.items()))
    return res, ok


with sync_playwright() as p:
    OUT.mkdir(exist_ok=True)
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1512, "height": 982})   # MBP 14"
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.add_init_script(HOOK)
    pg.add_init_script(f"localStorage.setItem('lastWork', '{SLUG}'); localStorage.setItem('displayMode', 'horizontal'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.goto(URL)
    pg.wait_for_function(f"window.SP && SP.ready && SP.slug === '{SLUG}' && SP.chunks.length > 0", timeout=180000)
    orig = pg.evaluate("({ countIn: countIn.checked, loopOn: loopOn.checked, sound: soundMode.value, bar: SP.steps[SP.pos].measure + 1, follow: SP.hFollow })")
    pg.evaluate("countIn.checked = false; countIn.dispatchEvent(new Event('change')); if (loopOn.checked) { loopOn.checked = false; loopOn.dispatchEvent(new Event('change')); } soundMode.value = 'pure'; soundMode.dispatchEvent(new Event('change'));")
    speed = pg.evaluate("+bpm.value")

    rows, fails = [], 0
    for policy in POLICIES:
        res, ok = measure(pg, policy, 1512)
        rows.append(res)
        fails += sum(1 for v in ok.values() if not v)

    # 還原設定
    pg.fill("#gotoBar", GOTO); pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    pg.evaluate(f"bpm.value = {speed}; bpm.dispatchEvent(new Event('input'));")
    pg.fill("#gotoBar", f"#{orig['bar']}"); pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    pg.evaluate(f"hFollow.value = {json.dumps(orig['follow'])}; hFollow.dispatchEvent(new Event('change'));")
    pg.evaluate(f"countIn.checked = {json.dumps(orig['countIn'])}; countIn.dispatchEvent(new Event('change')); soundMode.value = {json.dumps(orig['sound'])}; soundMode.dispatchEvent(new Event('change')); if (loopOn.checked !== {json.dumps(orig['loopOn'])}) {{ loopOn.checked = {json.dumps(orig['loopOn'])}; loopOn.dispatchEvent(new Event('change')); }}")
    pg.wait_for_timeout(1200)
    b.close()

print("\n比較（前面看得到幾小節越多越好；游標 x 範圍越小閱讀位置越固定；動的幀數越少越安定）")
print(f"{'方式':8} {'lookahead 最少':>14} {'平均':>6} {'游標 x 範圍':>12} {'動的幀':>12} {'最大移動':>9}")
for r in rows:
    print(f"{r['policy']:8} {r['aheadMin']:>14} {r['aheadAvg']:>6} {r['cursorRange']:>12} {r['movingFrames']:>12} {r['maxMove']:>9}")
if errs:
    print("page errors:", errs[:3])
raise SystemExit(1 if (fails or errs) else 0)
