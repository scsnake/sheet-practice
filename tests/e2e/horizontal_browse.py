"""橫向模式、沒在播放時往右捲著看：

1. 一路捲到樂章尾巴都要接得上。以前會卡住的兩個原因：
   - 游標所在的塊被強制留著，塊數到上限後新排的塊會立刻被丟掉
   - 切片從第 200 小節開始時 OSMD 排版出錯（第一小節的力度記號），重試只會縮短結尾，永遠排不出來
2. 到了樂章尾巴再往右推（滾輪／觸控板），或按「下一頁」，就換成下一個樂章

需要伺服器在 8777（./run.sh）。
"""
from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK

SLUG = WORK
fails = []
def check(name, ok, detail=""):
    print(("OK  " if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page(viewport={"width": 1500, "height": 900})
    pg.add_init_script(f"localStorage.setItem('lastWork', '{SLUG}'); localStorage.setItem('displayMode', 'horizontal'); localStorage.setItem('toolbarAutoHide', 'off'); localStorage.setItem('sp.settings.{SLUG}', JSON.stringify({{posMeasure: 120, measures: 835}}))")
    gave_up = []
    pg.on("console", lambda m: "追加樂譜失敗" in m.text and gave_up.append(m.text[:200]))
    pg.goto("http://localhost:8777/")
    pg.wait_for_function(f"window.SP && SP.slug==='{SLUG}' && SP.ready && SP.steps.length>1000", timeout=240000); pg.wait_for_timeout(1500)
    sec_end = pg.evaluate("SP.sections[0].end")
    last_to = lambda: pg.evaluate("SP.chunks[SP.chunks.length-1].to")
    first_from = lambda: pg.evaluate("SP.chunks[0].from")
    for _ in range(60):   # 用捲軸（不是滾輪）往右捲，直到樂章尾巴排好、而且捲到底
        pg.evaluate("scoreWrap.scrollLeft += scoreWrap.clientWidth * 0.8"); pg.wait_for_timeout(600)
        if last_to() == sec_end and pg.evaluate("scoreWrap.scrollLeft >= scoreWrap.scrollWidth - scoreWrap.clientWidth - 2"): break
    check("沒有排版失敗到放棄", not gave_up, str(gave_up[:1]))
    check("捲到樂章尾巴", last_to() == sec_end, f"last={last_to()} end={sec_end}")
    check("游標沒動", pg.evaluate("SP.steps[SP.pos].measure") == 120)
    pg.mouse.move(700, 500)
    pg.mouse.wheel(200, 0); pg.wait_for_timeout(200)
    check("剛到底推一下：還不換", first_from() <= sec_end)
    pg.wait_for_timeout(800)
    for _ in range(4): pg.mouse.wheel(150, 0); pg.wait_for_timeout(50)
    try: pg.wait_for_function(f"SP.chunks.length && SP.chunks[0].from > {sec_end}", timeout=10000)
    except Exception: pass
    check("停在底部再往右推：換成下一樂章", first_from() == sec_end + 1, f"first={first_from()}")
    # 「下一頁」：再捲到第二樂章尾巴，按一下就換第三樂章
    sec2_end = pg.evaluate("SP.sections[1].end")
    for _ in range(60):
        pg.click("#pageNext"); pg.wait_for_timeout(600)
        if first_from() > sec2_end: break
    check("按下一頁：到尾巴後換成第三樂章", first_from() == sec2_end + 1, f"first={first_from()}")
    # 第 200 小節（切片從這裡開始時 OSMD 會出錯）：直接跳過去也要排得出來
    pg.fill("#gotoBar", "#200"); pg.keyboard.press("Enter")
    pg.wait_for_function("SP.chunks.some(v => v.from <= 199 && 199 <= v.to)", timeout=20000)
    check("跳到第 200 小節排得出來", True)
    b.close()
raise SystemExit(1 if fails else 0)
