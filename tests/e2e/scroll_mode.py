"""捲動模式（顯示：捲動）的瀏覽器測試。

1. 播放 SP_PLAY_SEC 秒（預設 30）：每幀間隔、DOM 裡的塊數、畫面有沒有突然跳動、游標是否一直在畫面內、
   音符遲到數、追加一塊前後的最長幀間隔
2. 播放中用滾輪捲動 → 暫停跟隨；跳小節 → 恢復
3. 在不是第一塊的地方：練習聲部底色位置、點擊跳小節、修正模式選音符（只選取，不修改）
4. 塊的最後一行兩種做法（cut：只留完整的行／stretch：拉滿）各截一張塊交界的圖到 out/

會暫時改速度、關掉預備拍／循環／跳過長休止、音色改純音，結束時還原。
"""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK

URL = "http://localhost:8777/"
OUT = Path(__file__).parent / "out"
PLAY_SEC = float(os.environ.get("SP_PLAY_SEC", "30"))
CHUNK_LIMIT = 6

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

# 每幀記錄：時間、塊數、游標小節、內容這一幀在畫面上移動多少（同一塊的位置變化）、各塊移動量是否一致、游標在畫面上的位置
REC = """() => {
  const token = (window.__tok = (window.__tok || 0) + 1);
  const wrap = document.getElementById('scoreWrap');
  window.__rec = []; window.__late = []; window.__osc = 0; window.__t0 = performance.now();
  let prev = null;
  const tick = () => {
    if (window.__tok !== token) return;
    const wr = wrap.getBoundingClientRect(), tops = new Map();
    for (const v of SP.chunks) tops.set(v, v.div.getBoundingClientRect().top);
    let move = 0, spread = 0;
    if (prev) { const ds = [...tops].filter(([v]) => prev.has(v)).map(([v, t]) => t - prev.get(v)); if (ds.length) { move = ds[0]; spread = Math.max(...ds) - Math.min(...ds); } }
    const cv = SP.cursorView, c = cv && cv.osmd.cursor; let cur = null;
    if (c && !c.hidden && c.cursorElement) { const r = c.cursorElement.getBoundingClientRect(); cur = [r.top - wr.top, r.bottom - wr.top]; }
    window.__rec.push({ t: +(performance.now() - window.__t0).toFixed(1), n: SP.chunks.length, m: SP.steps[SP.pos].measure, move: +move.toFixed(1), spread: +spread.toFixed(1), cur, h: wrap.clientHeight, paused: SP.follow.paused });
    prev = tops;
    if (window.__rec.length < 30000) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""
STOPREC = "() => { window.__tok = (window.__tok || 0) + 1; }"

# 某一塊中間那一小節：練習聲部譜表在畫面上的位置、點擊點、第一個音符
GEOM = """(from) => {
  const v = SP.chunks.find((c) => c.from === from); if (!v) return null;
  const m = v.from + Math.floor((v.to - v.from) / 2), u = 10 * SP.zoom, r = v.div.getBoundingClientRect();
  const staff = SP.parts[SP.practice].staves[0], gm = v.osmd.GraphicSheet.MeasureList[m - v.from][staff];
  const sl = gm.ParentStaffLine.PositionAndShape.AbsolutePosition, ap = gm.PositionAndShape.AbsolutePosition, w = gm.PositionAndShape.Size.width;
  let note = null, any = null;
  for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) {
    const p = gn.PositionAndShape.AbsolutePosition, pt = { x: r.left + p.x * u, y: r.top + p.y * u };
    if (!any) any = pt;
    if (!note && !gn.sourceNote.isRest()) note = pt;
  }
  return { m, click: { x: r.left + (ap.x + w / 2) * u, y: r.top + (sl.y + 2) * u }, mine: { left: r.left + ap.x * u, top: r.top + (sl.y - 1.5) * u }, note: note || any };
}"""
CENTER_ON = """(y) => { SP.follow.paused = true; const w = document.getElementById('scoreWrap'), wr = w.getBoundingClientRect(); w.scrollTop += y - (wr.top + w.clientHeight / 2); }"""
MINE_AT = """([left, top]) => [...document.querySelectorAll('#overlay .mine')].some((d) => { const r = d.getBoundingClientRect(); return Math.abs(r.left - left) < 1.5 && Math.abs(r.top - top) < 1.5; })"""
SEL_CENTER = """() => { const d = document.querySelector('#overlay .sel'); if (!d) return null; const r = d.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }"""
# 選取標記離該小節（所有聲部）最近的符頭多遠：OSMD 的 GetNearestNote 可能挑到相鄰譜表的音，所以不要求是點的那個
NOTE_DIST = """([from, m, x, y]) => { const v = SP.chunks.find((c) => c.from === from), u = 10 * SP.zoom, r = v.div.getBoundingClientRect(); let best = Infinity;
  for (const gm of v.osmd.GraphicSheet.MeasureList[m - v.from]) if (gm) for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) { const p = gn.PositionAndShape.AbsolutePosition; best = Math.min(best, Math.hypot(r.left + p.x * u - x, r.top + p.y * u - y)); }
  return +best.toFixed(1); }"""
CURSOR_VISIBLE = """() => { const w = document.getElementById('scoreWrap').getBoundingClientRect(), c = SP.cursorView && SP.cursorView.osmd.cursor; if (!c || c.hidden) return false; const r = c.cursorElement.getBoundingClientRect(); return Math.min(r.bottom, w.bottom) - Math.max(r.top, w.top) >= 30; }"""
CHUNKS = "JSON.stringify(SP.chunks.map((v) => [v.from + 1, v.to + 1, v.div.offsetHeight]))"


def visible(r):
    c = r["cur"]
    return c is not None and min(c[1], r["h"]) - max(c[0], 0) >= 30


def analyze(label, rec, stats, late):
    gaps = [(round(rec[i]["t"] - rec[i - 1]["t"], 1), rec[i]["t"]) for i in range(1, len(rec))]
    worst = max(gaps) if gaps else (0, 0)
    end = rec[-1]["t"]
    built = [s for s in stats if 0 <= s["at"] <= end + 50]
    near = []
    for s in built:
        g = [gap for gap, t in gaps if s["at"] - s["ms"] - 50 <= t <= s["at"] + 150]
        near.append(max(g) if g else 0)
    jumps = [r for r in rec[1:] if abs(r["move"]) > 150 or r["spread"] > 2]
    longest, start = 0.0, None
    for r in rec:
        if not visible(r):
            start = r["t"] if start is None else start
            longest = max(longest, r["t"] - start)
        else:
            start = None
    out = sum(1 for r in rec if not visible(r))
    max_n = max(r["n"] for r in rec)
    late_after = [l for t, l in late if t > 1000]
    bars = sorted({r["m"] + 1 for r in rec})
    print(f"  [{label}] played bars {bars[0]}–{bars[-1]}, frames {len(rec)}, worst frame gap {worst[0]} ms at {worst[1]:.0f} ms")
    print(f"    chunks appended while playing: {len(built)}  " + ", ".join(f"{s['from'] + 1}-{s['to'] + 1} load {s['loadMs']} + render {s['renderMs']} ms (worst gap nearby {g} ms)" for s, g in zip(built, near)))
    print(f"    max chunks in DOM {max_n}; jump frames {len(jumps)} {[(r['t'], r['move'], r['spread']) for r in jumps[:5]]}; max per-frame move {max(abs(r['move']) for r in rec):.1f} px")
    print(f"    cursor out of view: {out} frames, longest {longest:.0f} ms; notes started late >20 ms after the first second: {len(late_after)} (max {max(late_after) if late_after else 0} s)")
    return {
        "chunks<=6": max_n <= CHUNK_LIMIT,
        "no jumps": not jumps,
        "append gap<120ms": (max(near) if near else 0) < 120,
        "cursor in view": out == 0,
        "no late notes": not late_after,
        "appended": len(built) > 0,
    }


def scenario(p, slug, goto, speed, chunk_mode, checks):
    label = f"{slug} {chunk_mode}"
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1500, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append((e.stack or str(e))[:900]))
    pg.add_init_script(HOOK)
    pg.add_init_script(f"localStorage.setItem('lastWork', '{slug}'); localStorage.setItem('displayMode', 'scroll'); localStorage.setItem('toolbarAutoHide', 'off'); localStorage.setItem('scrollChunk', '{chunk_mode}')")
    pg.goto(URL)
    pg.wait_for_function(f"window.SP && SP.ready && SP.slug === '{slug}' && SP.chunks.length > 0", timeout=120000)
    pg.wait_for_timeout(1500)
    orig_bar = pg.evaluate("SP.steps[SP.pos].measure + 1")
    orig = pg.evaluate("({countIn: countIn.checked, loopOn: loopOn.checked, skipSolo: skipSolo.checked, soundMode: soundMode.value})")
    pg.evaluate("countIn.checked = false; countIn.dispatchEvent(new Event('change')); if (loopOn.checked) { loopOn.checked = false; loopOn.dispatchEvent(new Event('change')); } if (skipSolo.checked) { skipSolo.checked = false; skipSolo.dispatchEvent(new Event('change')); } soundMode.value = 'pure'; soundMode.dispatchEvent(new Event('change'));")
    pg.fill("#gotoBar", goto); pg.keyboard.press("Enter")
    pg.wait_for_timeout(1500)
    orig_speed = pg.evaluate("+bpm.value")
    pg.evaluate(f"bpm.value = {speed}; bpm.dispatchEvent(new Event('input'));")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.wait_for_timeout(3000)
    print(f"{label}: paused at bar {pg.evaluate('SP.steps[SP.pos].measure + 1')}, scoreWrap height {pg.evaluate('scoreWrap.clientHeight')} px, chunks [from, to, height px] {pg.evaluate(CHUNKS)}")
    print("  builds so far:", json.dumps(pg.evaluate("SP.chunkStats.map((s) => [s.from + 1, s.to + 1, s.dir, s.loadMs, s.renderMs])")))

    # 塊交界截圖（cut／stretch 比較用）
    pg.evaluate("() => { const cs = SP.chunks, i = cs.findIndex((v) => v === SP.cursorView); const v = cs[Math.max(1, i + 1)] || cs[cs.length - 1]; SP.follow.paused = true; scoreWrap.scrollTop = score.offsetTop + v.div.offsetTop - scoreWrap.clientHeight / 2; }")
    pg.wait_for_timeout(1500)
    shot = OUT / f"scroll_{slug}_{chunk_mode}_boundary.png"
    pg.locator("#scoreWrap").screenshot(path=str(shot))
    print(f"  boundary screenshot: {shot.name}")
    pg.fill("#gotoBar", goto); pg.keyboard.press("Enter")   # 回到起點（也恢復跟隨）
    pg.wait_for_timeout(2000)

    result = {}
    pg.evaluate(REC)
    pg.keyboard.press("Space")
    pg.wait_for_timeout(PLAY_SEC * 1000)
    pg.keyboard.press("Space")
    pg.evaluate(STOPREC)
    rec = pg.evaluate("window.__rec")
    stats = pg.evaluate("SP.chunkStats.map((s) => ({ ...s, at: s.at - window.__t0 }))")
    late = pg.evaluate("window.__late.map(([t, l]) => [t - window.__t0, l])")
    result.update(analyze(label, rec, stats, late))

    if checks:
        # 播放中滾輪往上捲 → 暫停跟隨，畫面停在使用者捲到的地方；跳小節 → 恢復
        pg.keyboard.press("Space"); pg.wait_for_timeout(2500)
        pg.mouse.move(750, 650); pg.mouse.wheel(0, -700); pg.wait_for_timeout(700)
        ref = pg.evaluate("() => { const v = SP.chunks[SP.chunks.length - 1]; return { from: v.from, top: v.div.getBoundingClientRect().top, m: SP.steps[SP.pos].measure, paused: SP.follow.paused }; }")
        pg.wait_for_timeout(3000)
        later = pg.evaluate(f"() => {{ const v = SP.chunks.find((c) => c.from === {ref['from']}); return {{ top: v ? v.div.getBoundingClientRect().top : null, m: SP.steps[SP.pos].measure, paused: SP.follow.paused }}; }}")
        held = ref["paused"] and later["paused"] and later["top"] is not None and abs(later["top"] - ref["top"]) < 3 and later["m"] > ref["m"]
        print(f"  wheel while playing: paused={later['paused']}, view moved {None if later['top'] is None else round(later['top'] - ref['top'], 1)} px while cursor went bar {ref['m'] + 1}→{later['m'] + 1}")
        pg.fill("#gotoBar", f"#{later['m'] + 3}"); pg.keyboard.press("Enter")
        pg.wait_for_timeout(2000)
        resumed = pg.evaluate("({ paused: SP.follow.paused, m: SP.steps[SP.pos].measure })")
        vis = pg.evaluate(CURSOR_VISIBLE)
        print(f"  after goto #{later['m'] + 3}: paused={resumed['paused']}, cursor at bar {resumed['m'] + 1}, cursor visible={vis}")
        pg.keyboard.press("Space"); pg.wait_for_timeout(1500)
        result["wheel pauses follow"] = held
        result["goto resumes follow"] = (not resumed["paused"]) and vis

        # 不是第一塊的地方：練習聲部底色、點擊跳小節、修正模式選音符
        target = pg.evaluate("() => { const i = SP.chunks.indexOf(SP.cursorView); return (SP.chunks[i + 1] || SP.chunks[1] || SP.chunks[0]).from; }")
        g = pg.evaluate(GEOM, target)
        pg.evaluate(CENTER_ON, g["click"]["y"]); pg.wait_for_timeout(2000)
        g = pg.evaluate(GEOM, target)
        idx = pg.evaluate(f"SP.chunks.findIndex((c) => c.from === {target})")
        mine_ok = pg.evaluate(MINE_AT, [g["mine"]["left"], g["mine"]["top"]])
        pg.mouse.click(g["click"]["x"], g["click"]["y"]); pg.wait_for_timeout(1500)
        clicked = pg.evaluate("SP.steps[SP.pos].measure")
        print(f"  chunk #{idx} (starts bar {target + 1}), bar {g['m'] + 1}: practice-part overlay aligned={mine_ok}; click → cursor bar {clicked + 1}")
        result["overlay aligned"] = mine_ok
        result["click goes to bar"] = clicked == g["m"]

        pg.evaluate("SP.follow.paused = true")
        g = pg.evaluate(GEOM, target)
        pg.evaluate("editMode.checked = true; editMode.dispatchEvent(new Event('change'));"); pg.wait_for_timeout(300)
        g = pg.evaluate(GEOM, target)   # 修正模式提示列出現後版面往下移了
        pg.mouse.click(g["note"]["x"], g["note"]["y"]); pg.wait_for_timeout(400)
        sel = pg.evaluate("SP.selected && { measure: SP.selected.measure, part: SP.selected.part }")
        c = pg.evaluate(SEL_CENTER)
        d = None if not (c and sel) else pg.evaluate(NOTE_DIST, [target, sel["measure"], c["x"], c["y"]])
        print(f"  edit mode: clicked practice part {pg.evaluate('SP.practice')} note in bar {g['m'] + 1}, selected {sel}, selection mark {d} px from the nearest note head in that bar")
        pg.keyboard.press("Escape"); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        result["edit selects note"] = bool(sel) and sel["measure"] == g["m"] and d is not None and d < 3
        pg.screenshot(path=str(OUT / f"scroll_{slug}_{chunk_mode}_checks.png"))

        # 不跨樂章：停在第 1 樂章最後一小節時只排這個樂章；播進第 2 樂章後整個換成第 2 樂章
        secs = pg.evaluate("SP.sections.map((s) => [s.start, s.end])")
        if len(secs) > 1:
            in_secs = "() => { const sec = (m) => SP.sections.findIndex((s) => m >= s.start && m <= s.end); return [...new Set(SP.chunks.flatMap((v) => [sec(v.from), sec(v.to)]))].sort(); }"
            pg.fill("#gotoBar", f"#{secs[0][1] + 1}"); pg.keyboard.press("Enter"); pg.wait_for_timeout(4000)
            paused_secs = pg.evaluate(in_secs)
            pg.keyboard.press("Space")
            try:
                pg.wait_for_function(f"SP.steps[SP.pos].measure >= {secs[1][0]}", timeout=20000); pg.wait_for_timeout(2500); crossed = True
            except Exception:
                crossed = False
            play_secs, vis2, bar2 = pg.evaluate(in_secs), pg.evaluate(CURSOR_VISIBLE), pg.evaluate("SP.steps[SP.pos].measure + 1")
            pg.keyboard.press("Space"); pg.wait_for_timeout(500)
            print(f"  movement boundary: paused at bar {secs[0][1] + 1} → chunks in movements {paused_secs}; played into bar {bar2} → chunks in movements {play_secs}, cursor visible={vis2}")
            result["stays in movement"] = paused_secs == [0]
            result["next movement replaces chunks"] = crossed and play_secs == [1] and vis2

    # 還原設定
    pg.fill("#gotoBar", goto); pg.keyboard.press("Enter"); pg.wait_for_timeout(800)
    pg.evaluate(f"bpm.value = {orig_speed}; bpm.dispatchEvent(new Event('input'));")
    pg.fill("#gotoBar", f"#{orig_bar}"); pg.keyboard.press("Enter"); pg.wait_for_timeout(800)
    pg.evaluate(f"countIn.checked = {json.dumps(orig['countIn'])}; countIn.dispatchEvent(new Event('change')); skipSolo.checked = {json.dumps(orig['skipSolo'])}; skipSolo.dispatchEvent(new Event('change')); soundMode.value = {json.dumps(orig['soundMode'])}; soundMode.dispatchEvent(new Event('change')); if (loopOn.checked !== {json.dumps(orig['loopOn'])}) {{ loopOn.checked = {json.dumps(orig['loopOn'])}; loopOn.dispatchEvent(new Event('change')); }}")
    pg.wait_for_timeout(1500)
    result["no page errors"] = not errs
    if errs:
        print("  page errors:", errs[:3])
    b.close()
    return label, result


SCENARIOS = [
    (WORK, "3:1", 150, "cut", True),      # 第 3 樂章 3/8 拍，150% 時 30 秒約 58 小節：會追加、回收
    (WORK, "3:1", 150, "stretch", False),
    (SOLO_WORK, "#1", 150, "cut", True),  # 6 聲部（鋼琴兩行譜表）
]

with sync_playwright() as p:
    OUT.mkdir(exist_ok=True)
    only = os.environ.get("SP_ONLY", "")   # 例如 SP_ONLY=stretch 只跑名稱含 stretch 的情境
    results = [scenario(p, *s) for s in SCENARIOS if only in f"{s[0]} {s[3]}"]
print()
fails = 0
for label, r in results:
    bad = [k for k, v in r.items() if not v]
    fails += len(bad)
    print(f"{'PASS' if not bad else 'FAIL'} {label}: " + ", ".join(f"{k}={'ok' if v else 'NO'}" for k, v in r.items()))
raise SystemExit(1 if fails else 0)
