"""設定記在瀏覽器：重新整理回到同一小節、同速度；另一個瀏覽器不受影響；伺服器上的設定檔不被改動。

第一次開作品時沒有 localStorage，會用伺服器上的舊設定（data/_works/<slug>.settings.json）當預設。
需要伺服器在 8777（./run.sh）。
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
import json, os

SLUG = WORK
DISPLAY = os.environ.get("SP_DISPLAY", "page")
URL = "http://localhost:8777/"
data_dir = Path(os.environ.get("SP_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").expanduser()
server_file = data_dir / "_works" / f"{SLUG}.settings.json"
server_before = server_file.read_text() if server_file.exists() else None
server_st = json.loads(server_before) if server_before else {}

def open_page(ctx):
    pg = ctx.new_page()
    pg.add_init_script(f"localStorage.setItem('lastWork', '{SLUG}'); localStorage.setItem('displayMode', '{DISPLAY}'); localStorage.setItem('toolbarAutoHide', 'off')")
    pg.goto(URL); wait_ready(pg)
    return pg

def wait_ready(pg):
    pg.wait_for_function(f"window.SP && SP.slug==='{SLUG}' && SP.ready && SP.steps.length>1000", timeout=240000)
    pg.wait_for_timeout(500)

bar = lambda pg: pg.evaluate("SP.steps[SP.pos].measure")
stored = lambda pg: pg.evaluate(f"JSON.parse(localStorage.getItem('sp.settings.{SLUG}'))")
fails = []
def check(name, ok, detail=""):
    print(("OK  " if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    a = b.new_context(viewport={"width": 1500, "height": 900})
    pg = open_page(a)
    if server_st.get("measures") == pg.evaluate("SP.nMeasures"):
        check("第一次開：沿用伺服器的位置", bar(pg) == server_st.get("posMeasure", 0), f"bar={bar(pg)} server={server_st.get('posMeasure')}")
    # 跳到第 300 小節、改練習速度
    pg.fill("#gotoBar", "#300"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
    pg.evaluate("bpm.value = 70; bpm.dispatchEvent(new Event('input')); bpm.dispatchEvent(new Event('change'))")
    pg.wait_for_timeout(1200)
    st = stored(pg)
    check("存進 localStorage", st and st["posMeasure"] == 299, str(st and st["posMeasure"]))
    pg.reload(); wait_ready(pg)
    check("重新整理回到第 300 小節", bar(pg) == 299, f"bar={bar(pg)}")
    check("速度沿用", pg.evaluate("bpm.value") == "70", pg.evaluate("bpm.value"))
    # 播過幾小節後直接重新整理（不等節流），位置要跟著播放記下來；等小節而不是等秒數，才不受作品速度影響
    pg.click("#btnPlay"); pg.wait_for_function("SP.steps[SP.pos].measure >= 301", timeout=60000)
    played_to = bar(pg)
    pg.reload(); wait_ready(pg)
    check("播放中重新整理：回到播到的小節", abs(bar(pg) - played_to) <= 1 and played_to > 299, f"played_to={played_to} now={bar(pg)}")
    # 另一個瀏覽器（新的 context = 空的 localStorage）不受影響
    other = open_page(b.new_context(viewport={"width": 1500, "height": 900}))
    check("另一個瀏覽器不受影響", bar(other) != bar(pg), f"other={bar(other)} this={bar(pg)}")
    b.close()

check("伺服器設定檔沒被改", (server_file.read_text() if server_file.exists() else None) == server_before)
raise SystemExit(1 if fails else 0)
