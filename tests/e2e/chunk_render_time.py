import json
from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK
JS = r"""
async ([slug, from, sizes]) => {
  const xml = await fetch(`/api/works/${slug}/score.musicxml`).then(r => r.text());
  const src = new DOMParser().parseFromString(xml, 'application/xml');
  const slice = (a, b) => { const c = src.cloneNode(true); for (const p of c.querySelectorAll('score-partwise > part')) Array.from(p.children).filter(e => e.tagName === 'measure').forEach((m, i) => { if (i < a || i > b) m.remove(); }); return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(c); };
  const out = [];
  for (const n of sizes) {
    const x = slice(from, from + n - 1);
    const div = document.createElement('div'); div.style.width = '1450px'; document.body.appendChild(div);
    const o = new opensheetmusicdisplay.OpenSheetMusicDisplay(div, { autoResize: false, backend: 'svg', drawTitle: false, stretchLastSystemLine: false });
    await o.load(x);
    let t = performance.now(); o.render(); const ms = performance.now() - t;
    const systems = o.GraphicSheet.MusicPages.flatMap(p => p.MusicSystems);
    const barsPerSystem = systems.map(s => s.StaffLines[0].Measures.length);
    out.push({ bars: n, renderMs: Math.round(ms), systems: systems.length, barsPerSystem, heightPx: Math.round(div.getBoundingClientRect().height) });
    div.remove();
  }
  return JSON.stringify(out);
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page(viewport={"width": 1500, "height": 900})
    pg.goto("http://localhost:8777/api/works"); pg.add_script_tag(url="http://localhost:8777/static/vendor/opensheetmusicdisplay.min.js")
    for slug, frm in [(WORK, 0), (SOLO_WORK, 0)]:
        pg.evaluate(JS, [slug, frm, [4, 8, 12, 24]])  # warm-up
        rows = json.loads(pg.evaluate(JS, [slug, frm, [4, 8, 12, 24]]))
        for r in rows: print(slug, r)
    b.close()
