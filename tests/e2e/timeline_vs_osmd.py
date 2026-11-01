"""伺服器端 timeline.json 和 OSMD 自己分析整份譜的結果逐項比對。"""
import json
from playwright.sync_api import sync_playwright
from common import SOLO_WORK, WORK

JS = r"""
async (slug) => {
  const O = opensheetmusicdisplay;
  const [xml, tl] = await Promise.all([fetch(`/api/works/${slug}/score.musicxml`).then(r => r.text()), fetch(`/api/works/${slug}/timeline.json`).then(r => r.json())]);
  const div = document.createElement('div'); div.style.display = 'none'; document.body.appendChild(div);
  const osmd = new O.OpenSheetMusicDisplay(div, { autoResize: false, backend: 'svg' });
  await osmd.load(xml);
  const sheet = osmd.Sheet;
  sheet.SelectionStart = new O.Fraction(0, 1); sheet.SelectionEnd = sheet.SheetEndTimestamp;
  const it = sheet.MusicPartManager.getIterator(new O.Fraction(0, 1));
  const ins = sheet.Instruments, osteps = [];
  const xdoc = new DOMParser().parseFromString(xml, 'application/xml');
  const chrom = Array.from(xdoc.querySelectorAll('score-partwise > part')).map(p => { const tr = p.querySelector('measure > attributes > transpose'); return tr ? +(tr.querySelector('chromatic')?.textContent || 0) + 12 * +(tr.querySelector('octave-change')?.textContent || 0) : 0; });
  let graceSkipped = 0;
  while (!it.EndReached) {
    const beat = it.CurrentEnrolledTimestamp.RealValue * 4, m = it.CurrentMeasureIndex, notes = [];
    for (const ve of it.CurrentVoiceEntries || []) {
      const part = ins.indexOf(ve.ParentVoice.Parent);
      for (const n of ve.Notes) {
        if (n.isRest()) continue;
        if (ve.IsGrace || n.IsCueNote) { graceSkipped++; continue; }
        if (n.NoteTie && n.NoteTie.StartNote !== n) continue;
        let len = n.Length.RealValue;
        if (n.NoteTie && n.NoteTie.StartNote === n) len = n.NoteTie.Notes.reduce((a, x) => a + x.Length.RealValue, 0);
        notes.push([n.halfTone + 12 + (chrom[part] || 0), len * 4, part]);
      }
    }
    osteps.push([beat, m, notes]);
    it.moveToNext();
  }
  const ms = sheet.SourceMeasures;
  const res = { slug, nMeasures: [ms.length, tl.nMeasures], steps: [osteps.length, tl.steps.length], graceOrCueSkippedInOsmd: graceSkipped };
  const diffs = (name, a, b, tol) => { const d = []; for (let i = 0; i < Math.max(a.length, b.length); i++) { const x = a[i], y = b[i]; const bad = Array.isArray(x) ? JSON.stringify(x) !== JSON.stringify(y) : Math.abs((x ?? NaN) - (y ?? NaN)) > tol || x === undefined || y === undefined; if (bad) d.push([i + 1, x, y]); } res[name] = { count: d.length, first: d.slice(0, 4) }; };
  diffs('measureStart', ms.map(x => x.AbsoluteTimestamp.RealValue * 4), tl.measureStartBeat, 1e-6);
  let cur = 0; diffs('tempo', ms.map(x => { if (x.TempoInBPM > 0) cur = x.TempoInBPM; return cur || 100; }), tl.tempo, 0.5);
  diffs('timeSig', ms.map(x => x.ActiveTimeSignature ? [x.ActiveTimeSignature.Numerator, x.ActiveTimeSignature.Denominator] : [4, 4]), tl.timeSig, 0);
  const key = (b) => b.toFixed(4);
  const om = new Map(osteps.map(s => [key(s[0]), s])), sm = new Map(tl.steps.map(s => [key(s[0]), s]));
  const onlyO = [...om.keys()].filter(k => !sm.has(k)), onlyS = [...sm.keys()].filter(k => !om.has(k));
  res.onsetsOnlyInOsmd = { count: onlyO.length, first: onlyO.slice(0, 4) };
  res.onsetsOnlyInServer = { count: onlyS.length, first: onlyS.slice(0, 4) };
  let pitchBad = [], durBad = [], measBad = 0, noteCount = 0;
  for (const [k, o] of om) {
    const t = sm.get(k); if (!t) continue;
    if (o[1] !== t[1]) measBad++;
    const pa = o[2].map(x => `${x[2]}:${x[0]}`).sort().join(','), pb = t[4].map(x => `${x[2]}:${x[0]}`).sort().join(',');
    noteCount += o[2].length;
    if (pa !== pb) { pitchBad.push([k, o[1] + 1, pa.slice(0, 80), pb.slice(0, 80)]); continue; }
    const da = o[2].map(x => `${x[2]}:${x[0]}:${x[1].toFixed(4)}`).sort().join(','), db = t[4].map(x => `${x[2]}:${x[0]}:${(+x[1]).toFixed(4)}`).sort().join(',');
    if (da !== db) durBad.push([k, o[1] + 1, da.slice(0, 80), db.slice(0, 80)]);
  }
  res.notesCompared = noteCount;
  res.measureIndexMismatch = measBad;
  res.pitchMismatch = { count: pitchBad.length, first: pitchBad.slice(0, 3) };
  res.durationMismatch = { count: durBad.length, first: durBad.slice(0, 3) };
  return JSON.stringify(res);
}
"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto("http://localhost:8777/api/works")
    pg.add_script_tag(url="http://localhost:8777/static/vendor/opensheetmusicdisplay.min.js")
    for slug in [WORK, SOLO_WORK]:
        r = json.loads(pg.evaluate(JS, slug))
        print(json.dumps(r, ensure_ascii=False))
    b.close()
