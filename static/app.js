/* 分譜跟奏練習：OSMD 顯示 + Web Audio 多聲部播放 + 區段循環 + 拍數檢查 + 簡易音符修正 */
(() => {
  const OSMD = window.opensheetmusicdisplay;
  const $ = (id) => document.getElementById(id);
  const UNIT = 10; // OSMD 座標單位 → 像素（zoom = 1）
  const WIN = 40;  // 一頁最多幾小節；實際依譜表數縮小（排版時間大約和「小節×譜表」成正比）
  const winSize = () => S.winSize || WIN;  // 一次只排版這麼多小節（四聲部 800 小節全排要數十秒）

  // ---------- 狀態 ----------
  const S = {
    slug: null, work: null, workInfo: null,
    xmlDoc: null, xmlText: "",
    osmd: null,           // 目前顯示中那一頁的 OSMD
    pool: new Map(),      // 已排版（或排版中）的頁：from → {osmd, div, from, to, ready}
    activeView: null,
    winSize: 0,
    full: null,           // 隱藏的整份樂譜（不排版）：時間軸、速度、拍號都從這裡來；有快取時不需要
    ready: false,         // 時間軸準備好了才能播放
    nMeasures: 0, timeSig: [], measureTs: [],
    parts: [],            // [{index, name, xmlId, mute, preset}]
    practice: 0,          // 練習聲部 index
    steps: [],            // [{beat, srcTs(Fraction), measure, notes:[{midi, beats, part}]}]
    measureFirstStep: [], measureStartBeat: [], totalBeats: 0,
    ticks: [],
    errors: [],           // [{part, measure, actual, expected, empty}]
    speed: 1, speeds: [], tempo: [], measureStartSec: [],   // 每小節譜上速度；speeds = 每個樂章各自的練習速度（原速的倍數）；measureStartSec 已含練習速度
    loop: null, loopOn: false,
    pos: 0, cursorStep: -1,
    playing: false, segments: [], schedTimer: null, raf: null, finishAt: null,
    clickMode: "off", clickRanges: [],   // [[start,end]] 0-based inclusive
    countIn: true, volume: 0.8, myVolume: 1, zoom: 1, tuning: 442,   // A4 的頻率（Hz）
    soundMode: "pure", panning: true,
    skipSolo: false, skipMin: 8, skipKeep: 2, skips: [],   // 跳過「所有非獨奏聲部都休息超過 N 小節」的段落，保留最後 M 小節當提示   // 音色模式：strings / ensemble / organ / reed / pure；各聲部左右分佈
    edit: false, selected: null, undo: [],
    win: { from: -1, to: -1 },   // 翻頁：顯示中那一頁；捲動：DOM 裡所有塊合起來的範圍
    display: ["page", "horizontal"].includes(localStorage.getItem("displayMode")) ? localStorage.getItem("displayMode") : "scroll",   // 捲動／翻頁／橫向
    hFollow: localStorage.getItem("hFollow") || "step",   // 橫向跟隨：step 逐小節／smooth 連續／jump 整頁跳／half 半頁跳
    zoomH: 0.8,           // 橫向模式自己的縮放（一畫面看得到幾小節）
    chunks: [], chunkGen: 0, chunkSize: 12, chunkStats: [],   // 捲動模式：由上到下的塊（每塊一個 OSMD）
    chunkMode: localStorage.getItem("scrollChunk") === "stretch" ? "stretch" : "cut",   // 塊的最後一行：cut = 只留完整的行，stretch = 拉滿
    cursorView: null,     // 游標所在的頁／塊
    follow: { paused: false, y: null, target: null, base: null, next: null, m: -1, t: 0, dirty: true, raf: null, frames: 0 },   // 捲動模式的平滑跟隨
    beatNow: 0, layoutWidth: 0,
    sections: [],         // 樂章／段落：[{start, end, title}]（依匯入時的換頁記號）
    saveTimer: null, pollTimer: null,
    saveScore: false,     // 待存的修改是否包含譜本身
    settingsFor: null,    // 目前 S 裡的設定屬於哪個作品（套用前不存，免得把上一首的設定寫到新作品）
    savedMeasure: -1,
  };

  // ---------- 音訊 ----------
  let ctx = null, player = null;
  const PRESETS = {
    violin: "_tone_0400_FluidR3_GM_sf2_file", viola: "_tone_0410_FluidR3_GM_sf2_file",
    cello: "_tone_0420_FluidR3_GM_sf2_file", bass: "_tone_0430_FluidR3_GM_sf2_file",
    piano: "_tone_0000_FluidR3_GM_sf2_file",
    ensemble: "_tone_0490_FluidR3_GM_sf2_file", organ: "_tone_0190_FluidR3_GM_sf2_file", reed: "_tone_0200_FluidR3_GM_sf2_file",
  };
  const PRESET_CENTS = { organ: -6.7, reed: -1.7, ensemble: -2.3, piano: -9.5, bass: -0.4, violin: 0, viola: 0, cello: 0 };  // 取樣本身的走音，實測後補正
  const panners = [];        // 每個聲部一個 StereoPanner
  const synthVoices = [];    // 純音模式排程中的振盪器，停止時要關掉
  function presetFor(name) {
    const n = (name || "").toLowerCase();
    if (/piano|pianoforte|klavier|鋼琴/.test(n)) return "piano";
    if (/wind|brass|horn|trumpet|trombone|oboe|clarinet|bassoon|flute|管樂|銅管|木管/.test(n)) return "reed";   // 管樂合併聲部：簧風琴音色，和弦樂分得開
    if (/viola|中提/.test(n)) return "viola";
    if (/cello|violoncello|大提/.test(n)) return "cello";
    if (/contrabass|double bass|低音|bass/.test(n)) return "bass";
    return "violin";
  }
  function ensureAudio() {
    if (ctx) { if (ctx.state === "suspended") ctx.resume(); return; }
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    player = new WebAudioFontPlayer();
    for (const v of Object.values(PRESETS)) player.loader.decodeAfterLoading(ctx, v);
  }
  function panFor(i) {
    const n = S.parts.length;
    if (!S.panning || n < 2) return 0;
    return -0.75 + 1.5 * i / (n - 1);   // 由左到右：Vn I、Vn II、Vla、Vc、Cb
  }
  function outputFor(i) {
    if (!panners[i]) { panners[i] = ctx.createStereoPanner(); panners[i].connect(ctx.destination); }
    panners[i].pan.value = panFor(i);
    return panners[i];
  }
  let pureWave = null;
  function synthNote(target, when, freq, dur, vol) {
    // 純音：基音加上遞減的 1–8 次泛音（沒有抖音、音高穩定）。
    // 太接近正弦的音會被聽成偏低、也難分辨八度，所以泛音要夠，但仍比取樣弦樂乾淨。
    if (!pureWave) {
      const n = 9, re = new Float32Array(n), im = new Float32Array(n);
      for (let k = 1; k < n; k++) im[k] = 1 / Math.pow(k, 1.4);
      pureWave = ctx.createPeriodicWave(re, im, { disableNormalization: false });
    }
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(vol * 0.3, when + 0.025);
    g.gain.setValueAtTime(vol * 0.3, when + Math.max(0.025, dur - 0.05));
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    g.connect(target);
    const o = ctx.createOscillator();
    o.setPeriodicWave(pureWave); o.frequency.value = freq;
    o.connect(g); o.start(when); o.stop(when + dur + 0.02);
    synthVoices.push({ g, oscs: [o], end: when + dur });
    if (synthVoices.length > 400) synthVoices.splice(0, synthVoices.length - 400);
  }
  function stopSynth() {
    const now = ctx ? ctx.currentTime : 0;
    for (const v of synthVoices) { try { v.g.gain.cancelScheduledValues(now); v.g.gain.setValueAtTime(v.g.gain.value, now); v.g.gain.exponentialRampToValueAtTime(0.0001, now + 0.03); for (const o of v.oscs) o.stop(now + 0.05); } catch (e) { /* 已結束 */ } }
    synthVoices.length = 0;
  }
  function presetNameFor(part) {
    if (S.soundMode === "strings") return part.preset;
    return S.soundMode;   // ensemble / organ / reed：所有聲部同一音色
  }
  function tick(when, accent) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = accent ? 1600 : 1000;
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(0.5 * S.volume + 0.01, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 0.06);
    o.connect(g).connect(ctx.destination);
    o.start(when); o.stop(when + 0.08);
    synthVoices.push({ g, oscs: [o], end: when + 0.08 });   // 暫停時要能停掉已排好的拍點
  }
  function playNote(n, when, dur) {
    const part = S.parts[n.part];
    if (!part || part.mute) return;
    let vol = S.volume;
    if (n.part === S.practice) vol *= S.myVolume;
    if (vol <= 0.001) return;
    const detune = 12 * Math.log2(S.tuning / 440); // A=442 → 每個音高 +0.078 半音
    const target = outputFor(n.part);
    if (S.soundMode === "pure") { synthNote(target, when, 440 * Math.pow(2, (n.midi + detune - 69) / 12), dur, vol); return; }
    const pname = presetNameFor(part);
    player.queueWaveTable(ctx, target, window[PRESETS[pname]], when, n.midi + detune + (PRESET_CENTS[pname] || 0) / 100, dur, vol);
  }

  // ---------- 工具 ----------
  function measureAtBeat(beat) {
    let lo = 0, hi = S.measureStartBeat.length - 1, ans = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (S.measureStartBeat[mid] <= beat + 1e-6) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  const tempoAt = (m) => S.tempo[Math.max(0, Math.min(m, S.tempo.length - 1))] || 100;
  const speedAt = (m) => S.speeds[sectionOf(m)] ?? S.speed;               // 該小節所在樂章的練習速度
  const effTempo = (m) => tempoAt(m) * speedAt(m);                        // 實際播放的 ♩/分
  function T(beat) { // 拍 → 秒（含練習速度）
    const m = measureAtBeat(beat);
    return (S.measureStartSec[m] || 0) + (beat - (S.measureStartBeat[m] || 0)) * 60 / effTempo(m);
  }
  function Tinv(sec) { // 秒 → 拍
    let lo = 0, hi = S.measureStartSec.length - 1, m = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (S.measureStartSec[mid] <= sec + 1e-9) { m = mid; lo = mid + 1; } else hi = mid - 1; }
    return (S.measureStartBeat[m] || 0) + (sec - (S.measureStartSec[m] || 0)) * effTempo(m) / 60;
  }
  const secPerBeatAt = (m) => 60 / effTempo(m);
  function rebuildTimeMap() { // 每小節起始秒數（含各樂章的練習速度）
    const startSec = []; let sec = 0; const n = S.measureStartBeat.length;
    for (let m = 0; m < n; m++) { startSec.push(sec); const next = m + 1 < n ? S.measureStartBeat[m + 1] : S.totalBeats; sec += (next - S.measureStartBeat[m]) * 60 / effTempo(m); }
    S.measureStartSec = startSec;
  }
  function setStatus(msg, isError) {
    const el = $("status");
    if (!msg) { el.classList.add("hidden"); return; }
    el.textContent = msg; el.classList.toggle("error", !!isError); el.classList.remove("hidden");
  }
  function stepAtBeat(beat) {
    let lo = 0, hi = S.steps.length - 1, ans = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (S.steps[mid].beat <= beat + 1e-6) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  const measureOfStep = (k) => (S.steps[k] ? S.steps[k].measure : 0);
  const nMeasures = () => S.nMeasures;
  function loopRange() {
    if (S.loopOn && S.loop) {
      const s = S.measureFirstStep[S.loop.start] ?? 0;
      const endM = S.loop.end + 1;
      return [s, endM < S.measureStartBeat.length ? S.measureStartBeat[endM] : S.totalBeats];
    }
    return [0, S.totalBeats];
  }
  async function api(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
  }

  // ---------- 作品清單 / 上傳 / 管理 ----------
  async function refreshWorks(selectSlug) {
    const list = await api("/api/works");
    const sel = $("workSelect"); sel.innerHTML = "";
    if (!list.length) sel.innerHTML = "<option value=''>（尚無作品：上傳分譜，或見 docs/SCORES.md）</option>";
    $("workNames").innerHTML = "";
    for (const w of list) {
      const o = document.createElement("option"); o.value = w.slug;
      const pending = w.parts.filter((p) => p.status.state !== "done").length;
      o.textContent = `${w.work}（${w.parts.map((p) => p.part).join("、")}）` + (pending ? `　辨識中 ${pending} 個聲部` : "");
      sel.appendChild(o);
      const d = document.createElement("option"); d.value = w.work; $("workNames").appendChild(d);
    }
    const want = selectSlug || localStorage.getItem("lastWork");
    if (want && list.some((w) => w.slug === want)) sel.value = want; else if (list.length) sel.value = list[0].slug;
    if (sel.value && sel.value !== S.slug) loadWork(sel.value);
    return list;
  }
  $("workSelect").addEventListener("change", (e) => loadWork(e.target.value));

  $("btnUpload").addEventListener("click", () => { $("upWork").value = S.work || ""; $("upPart").value = ""; $("fileInput").value = ""; $("uploadDlg").showModal(); });
  $("fileInput").addEventListener("change", () => {
    const f = $("fileInput").files[0]; if (!f || $("upPart").value) return;
    const n = f.name.toLowerCase();
    const guess = /violin\s*1|violin\s*i(?![iv])|vn\.?\s*1|violin1/.test(n) ? "Violin I" : /violin\s*2|violin\s*ii|vn\.?\s*2|violin2|vn2/.test(n) ? "Violin II"
      : /viola/.test(n) ? "Viola" : /cello/.test(n) ? "Cello" : /bass/.test(n) ? "Contrabass" : "";
    $("upPart").value = guess;
  });
  $("doUpload").addEventListener("click", async (e) => {
    e.preventDefault();
    const f = $("fileInput").files[0]; if (!f) { alert("請選擇檔案"); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("work", $("upWork").value); fd.append("part", $("upPart").value);
    $("uploadDlg").close();
    setStatus(`上傳中：${f.name}`);
    try {
      const r = await api("/api/pieces", { method: "POST", body: fd });
      await refreshWorks(r.slug);
      if (r.slug === S.slug) loadWork(r.slug, true);
    } catch (err) { setStatus("上傳失敗：" + err.message, true); }
  });

  $("btnManage").addEventListener("click", async () => {
    const pieces = await api("/api/pieces");
    pieces.sort((a, b) => a.work.localeCompare(b.work) || a.order - b.order);
    const tb = $("manageTable").querySelector("tbody"); tb.innerHTML = "";
    for (const p of pieces) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td><input type="text" data-k="work" value="${esc(p.work)}"></td><td><input type="text" data-k="part" value="${esc(p.part)}"></td>
        <td><input type="number" data-k="order" value="${p.order}"></td><td>${p.measures ?? "–"}</td>
        <td>${p.status.state === "done" ? "完成" : p.status.state === "error" ? "失敗" : `${p.status.state} ${p.status.done ?? 0}/${p.status.total ?? "?"}`}</td>
        <td><button class="btn small" data-act="redo" title="重新辨識">重跑</button> <button class="btn small danger" data-act="del">刪除</button></td>`;
      tr.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", async () => {
        await api(`/api/pieces/${p.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ [inp.dataset.k]: inp.value }) });
        refreshWorks(S.slug); if (inp.dataset.k !== "work") loadWork(S.slug, true);
      }));
      tr.querySelector("[data-act=del]").addEventListener("click", async () => {
        if (!confirm(`刪除「${p.work} / ${p.part}」？`)) return;
        await api(`/api/pieces/${p.id}`, { method: "DELETE" }); tr.remove(); S.slug = null; refreshWorks();
      });
      tr.querySelector("[data-act=redo]").addEventListener("click", async () => { await api(`/api/pieces/${p.id}/reprocess`, { method: "POST" }); $("manageDlg").close(); refreshWorks(S.slug); loadWork(S.slug, true); });
      tb.appendChild(tr);
    }
    $("manageDlg").showModal();
  });
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

  async function loadWork(slug, force) {
    if (!slug) return;
    stop(false); clearTimeout(S.pollTimer);
    flushSave(); S.settingsFor = null;
    S.slug = slug; localStorage.setItem("lastWork", slug);
    $("downloadXml").href = `/api/works/${slug}/score.musicxml`;
    let info;
    try { info = await api(`/api/works/${slug}`); } catch (e) { setStatus("讀取作品失敗：" + e.message, true); return; }
    S.work = info.work; S.workInfo = info;
    renderPages(info);
    const pending = info.parts.filter((p) => p.status.state !== "done");
    if (pending.length) {
      const failed = pending.filter((p) => p.status.state === "error");
      setStatus(pending.map((p) => `${p.part}：${p.status.state === "error" ? "辨識失敗 " + p.status.error : `辨識中 ${p.status.done ?? 0}/${p.status.total || "?"} 頁`}`).join("　|　")
        + (failed.length === pending.length ? "" : "　（每頁約 15–30 秒，完成後自動載入）"), failed.length > 0);
      if (failed.length < pending.length) S.pollTimer = setTimeout(() => loadWork(slug, true), 4000);
      if (!info.parts.some((p) => p.status.state === "done")) return;
    } else setStatus("載入樂譜…");
    const counts = info.parts.filter((p) => p.measures != null).map((p) => `${p.part} ${p.measures}`);
    const distinct = new Set(info.parts.filter((p) => p.measures != null).map((p) => p.measures));
    try {
      S.t0 = performance.now();
      const [xr, tr, settings] = await Promise.all([fetch(`/api/works/${slug}/score.musicxml`), fetch(`/api/works/${slug}/timeline.json`), loadSettings(slug)]);
      if (!xr.ok) throw new Error(`${xr.status} ${await xr.text()}`);
      const stamp = xr.headers.get("x-score-stamp") || "";
      const [xml, timeline] = await Promise.all([xr.text(), tr.ok ? tr.json() : null]);
      // 時間軸和譜必須是同一個版本；對不上（例如剛好有分譜被改）就退回瀏覽器端分析
      const tl = timeline && tr.headers.get("x-score-stamp") === stamp ? timeline : null;
      S.pendingSettings = settings;
      S.stamp = stamp;
      await showScore(xml, false, tl);
      if (tl && !tl.verified) verifyTimeline(tl, stamp, slug);   // 第一次：空檔時用 OSMD 核對伺服器算的時間軸
      if (!pending.length) setStatus(distinct.size > 1 ? `各聲部小節數不一致（${counts.join("、")}），較短的聲部已用空小節補齊；請用拍數檢查找出多認／漏認的小節。` : "");
    } catch (e) { if (!pending.length) setStatus("載入失敗：" + e.message, true); }
  }

  function renderPages(info) {
    const el = $("pages"); el.innerHTML = "";
    for (const p of info.parts) {
      (p.pages || []).forEach((name, i) => {
        const h = document.createElement("h4"); h.textContent = `${p.part}　第 ${i + 1} 頁`;
        const img = document.createElement("img"); img.loading = "lazy"; img.src = `/api/pieces/${p.id}/pages/${name}`;
        el.append(h, img);
      });
    }
  }

  // ---------- 樂譜顯示 ----------
  function makeView(scroll, stretch, single) {   // scroll：捲動模式的塊；single：橫向模式，整塊排成一行
    const div = document.createElement("div"); div.className = "view rendering"; $("score").appendChild(div);
    const osmd = new OSMD.OpenSheetMusicDisplay(div, {
      autoResize: false, backend: "svg", drawTitle: false, drawPartNames: true, drawPartAbbreviations: true, useXMLMeasureNumbers: true,
      followCursor: !scroll, drawMeasureNumbers: true, renderMultipleRestMeasures: false, stretchLastSystemLine: !!stretch,
      renderSingleHorizontalStaffline: !!single,
      ...(single ? { drawPartNames: false, drawPartAbbreviations: false } : {}),
      ...(scroll ? { drawCredits: false, drawComposer: false, drawSubtitle: false, drawLyricist: false } : {}),   // 每塊開頭不必重複作曲者等文字
      cursorsOptions: [{ type: 0, color: "#2f6fed", alpha: 0.5, follow: true }],
    });
    return { osmd, div, from: -1, to: -1, ready: null };
  }
  const yieldToPaint = () => new Promise((r) => setTimeout(r, 30));
  function dropView(v) { try { v.osmd.clear(); } catch (e) { /* 還沒排版 */ } v.div.remove(); }
  function resetPool() {
    for (const v of S.pool.values()) dropView(v);
    S.pool.clear(); S.activeView = null; S.osmd = null; S.win = { from: -1, to: -1 }; S.cursorView = null;
  }
  const isScroll = () => S.display !== "page";       // 連續模式（直向／橫向）都用「塊」
  const horiz = () => S.display === "horizontal";   // 橫向：每塊排成一整行，時間往右走
  const zoomNow = () => (horiz() ? S.zoomH : S.zoom);
  function resetDisplay() { resetPool(); resetChunks(); }
  function showMeasure(m, anchor) { return isScroll() ? scrollShow(m, anchor) : renderWindow(m); }
  function redisplay() {   // 縮放、視窗大小改變：在目前位置重新排版
    const m = isScroll() ? (S.ready ? measureOfStep(S.pos) : (S.pendingMeasure ?? 0)) : Math.max(0, S.win.from);
    resetDisplay();
    return showMeasure(m);
  }
  function viewOf(m) {   // 畫面上（已排好）包含第 m 小節的頁或塊
    if (isScroll()) { for (const v of S.chunks) if (m >= v.from && m <= v.to) return v; return null; }
    const v = S.activeView; return v && m >= v.from && m <= v.to ? v : null;
  }
  let topCache = null;   // drawOverlay 期間快取每塊的位移，避免邊加覆蓋層邊量版面
  function viewOff(v) {  // 塊在 #score 裡的位移：直向是 top、橫向是 left（翻頁模式永遠是 0）
    if (!isScroll()) return 0;
    const read = () => (horiz() ? v.div.offsetLeft : v.div.offsetTop);
    if (!topCache) return read();
    let t = topCache.get(v); if (t === undefined) topCache.set(v, (t = read()));
    return t;
  }
  const viewSize = (v) => (horiz() ? v.div.offsetWidth : v.div.offsetHeight);
  function viewAtPoint(x, y) {
    if (!isScroll()) return S.activeView;
    const p = horiz() ? x : y;
    return S.chunks.find((v) => { const t = viewOff(v); return p >= t && p <= t + viewSize(v); }) || null;
  }
  async function showScore(xml, keepPos, timeline) {
    const tStart = performance.now();
    const lt = (S.loadTimes = { fetch: S.t0 && !keepPos ? Math.round(tStart - S.t0) : 0 }); let tt = tStart;
    const lap = (k) => { lt[k] = Math.round(performance.now() - tt); tt = performance.now(); };
    S.xmlText = xml;
    S.xmlDoc = new DOMParser().parseFromString(xml, "application/xml");
    lap("parse");
    const anchor = keepPos && isScroll() ? scrollAnchor(S.pendingMeasure) : null;   // 修改音符後重排：捲動位置不動
    resetDisplay();
    S.ready = false; S.steps = []; S.measureFirstStep = []; S.measureStartBeat = []; S.measureTs = []; S.timeSig = [];
    $("btnPlay").textContent = "…準備中"; $("btnPlay").disabled = true;
    buildParts();          // 聲部：從 XML 就能知道
    basicFromXml();        // 小節數、樂章、每頁小節數
    const clampM = (m) => Math.max(0, Math.min(m || 0, S.nMeasures - 1));
    const ps = S.pendingSettings;
    S.pendingMeasure = keepPos ? clampM(S.pendingMeasure ?? measureOfStep(S.pos)) : clampM(ps && ps.measures === S.nMeasures ? ps.posMeasure : 0);
    lap("partsSections");
    let source = "analyze";
    if (timeline && timeline.nMeasures === S.nMeasures) { restoreTimeline(timeline); source = "server"; finishTimeline(); }
    lap("timeline");
    await showMeasure(S.pendingMeasure, anchor);     // 第一頁／第一塊
    lap("firstRender");
    if (source !== "server") {                // 編輯後／沒有伺服器時間軸：在瀏覽器分析整份譜
      await yieldToPaint();
      if (!S.full) { const hidden = document.createElement("div"); hidden.style.display = "none"; document.body.appendChild(hidden); S.full = new OSMD.OpenSheetMusicDisplay(hidden, { autoResize: false, backend: "svg" }); }
      await S.full.load(xml);
      analyzeFull();
      finishTimeline();
      lap("analyze");
    }
    lt.source = source;
    lt.total = Math.round(performance.now() - (S.t0 && !keepPos ? S.t0 : tStart));
  }
  function finishTimeline() {
    rebuildTimeMap();
    buildTicks();
    checkBeats();
    if (S.pendingSettings) { applySettings(S.pendingSettings); S.pendingSettings = null; }
    S.settingsFor = S.slug;
    computeSkips();
    S.ready = true; $("btnPlay").disabled = false; $("btnPlay").textContent = "▶ 播放";
    S.cursorStep = -1;
    setCursor(S.measureFirstStep[S.pendingMeasure] ?? 0);
    $("loopStart").max = $("loopEnd").max = nMeasures();
    renderPartsUI(); drawOverlay();
  }
  function basicFromXml() {
    S.nMeasures = xmlMeasures(0).length;
    const staves = S.parts.reduce((a, p) => a + p.staves.length, 0) || 1;
    S.winSize = Math.max(12, Math.min(WIN, Math.round(120 / staves)));   // 5 聲部 → 24 小節一頁
    S.chunkSize = S.chunkMax = Math.max(12, Math.min(24, Math.round(60 / staves)));   // 捲動模式每塊試排的小節數：先用這個，之後依實測排版時間調整
    buildSections();
  }
  function restoreTimeline(c) {
    S.totalBeats = c.totalBeats; S.hasTempo = c.hasTempo; S.tempo = c.tempo; S.measureFirstStep = c.measureFirstStep; S.measureStartBeat = c.measureStartBeat;
    S.timeSig = c.timeSig; S.measureTs = c.measureTs.map(([n, d]) => new OSMD.Fraction(n, d));
    S.steps = c.steps.map(([beat, measure, n, d, notes]) => ({ beat, measure, srcTs: new OSMD.Fraction(n, d), notes: notes.map(([midi, beats, part]) => ({ midi, beats, part })) }));
  }
  function pageStart(m) { const sc = S.sections[sectionOf(m)]; const base = sc ? sc.start : 0; return base + Math.floor((m - base) / winSize()) * winSize(); }
  function sliceXml(from, to, noFirstDynamics) {
    // 只把 from–to 的小節複製到一份新文件（不複製整份譜），並把之前生效的 divisions／調號／拍號／譜號／譜表數帶到第一小節
    const src = S.xmlDoc, root = src.documentElement;
    const doc = document.implementation.createDocument(null, "score-partwise", null);
    const out = doc.documentElement;
    for (const child of root.children) { if (child.tagName !== "part" && child.tagName !== "part-list" && child.tagName !== "work" && child.tagName !== "movement-title" && child.tagName !== "credit") out.appendChild(doc.importNode(child, true)); }
    const pl = root.querySelector(":scope > part-list"); if (pl) out.appendChild(doc.importNode(pl, true));
    for (const part of root.querySelectorAll(":scope > part")) {
      const np = doc.createElement("part"); np.setAttribute("id", part.getAttribute("id")); out.appendChild(np);
      const measures = Array.from(part.children).filter((e) => e.tagName === "measure");
      const state = { divisions: null, key: null, time: null, staves: null, transpose: null, clefs: new Map() };
      for (let i = 0; i < from && i < measures.length; i++) {
        for (const a of measures[i].querySelectorAll(":scope > attributes")) for (const el of a.children) {
          if (el.tagName === "clef") state.clefs.set(el.getAttribute("number") || "1", el);
          else if (el.tagName in state) state[el.tagName] = el;
        }
      }
      for (let i = from; i <= to && i < measures.length; i++) np.appendChild(doc.importNode(measures[i], true));
      const first = np.firstElementChild; if (!first) continue;
      if (noFirstDynamics) for (const d of first.querySelectorAll(":scope > direction")) if (d.querySelector("dynamics")) d.remove();
      let attrs = first.querySelector(":scope > attributes");
      if (!attrs) { attrs = doc.createElement("attributes"); first.insertBefore(attrs, first.firstElementChild); }
      const have = new Set(Array.from(attrs.children).map((e) => e.tagName + (e.tagName === "clef" ? "#" + (e.getAttribute("number") || "1") : "")));
      const adds = [];
      for (const tag of ["divisions", "key", "time", "staves", "transpose"]) if (state[tag] && !have.has(tag)) adds.push(doc.importNode(state[tag], true));
      for (const [num, clef] of state.clefs) if (!have.has("clef#" + num)) adds.push(doc.importNode(clef, true));
      const order = { divisions: 0, key: 1, time: 2, staves: 3, "part-symbol": 4, instruments: 5, clef: 6, "staff-details": 7, transpose: 8 };
      const all = [...Array.from(attrs.children), ...adds].sort((x, y) => (order[x.tagName] ?? 9) - (order[y.tagName] ?? 9));
      for (const el of all) attrs.appendChild(el);
    }
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(doc);
  }
  function windowFor(m) {
    const n = nMeasures();
    let from = Math.max(0, Math.min(m, n - 1));
    from = pageStart(from);
    const sc = S.sections[sectionOf(from)];
    return { from, to: Math.min(from + winSize() - 1, sc ? sc.end : n - 1, n - 1) };
  }
  const POOL_MAX = 10;
  function getView(from, to) {   // 取得（必要時排版）某一頁；已排好的頁切換時是瞬間的
    let v = S.pool.get(from);
    if (v && v.to === to) { v.used = performance.now(); return v; }
    if (v) { dropView(v); S.pool.delete(from); }
    v = makeView(); v.from = from; v.to = to; v.used = performance.now();
    S.pool.set(from, v);
    v.ready = (async () => {
      if (S.playing) scheduler(2.5);                     // 切片、解析、排版都會佔住主執行緒：先排好之後 2.5 秒的聲音
      const xml = sliceXml(from, to);
      await v.osmd.load(xml);
      if (S.pool.get(from) !== v) return false;          // 期間被清掉（換作品、縮放）
      if (S.playing) scheduler(2.5);                     // 解析完、排版前再補一次
      v.osmd.Zoom = S.zoom;
      v.osmd.render();
      v.osmd.cursor?.hide();
      v.div.classList.remove("rendering");
      return true;
    })();
    evictPool();
    return v;
  }
  function evictPool() {
    if (S.pool.size <= POOL_MAX) return;
    const cur = Math.max(0, S.win.from);
    const cands = [...S.pool.values()].filter((v) => v !== S.activeView).sort((x, y) => Math.abs(y.from - cur) - Math.abs(x.from - cur));
    while (S.pool.size > POOL_MAX && cands.length) { const v = cands.shift(); dropView(v); S.pool.delete(v.from); }
  }
  let renderSeq = 0;
  async function renderWindow(m) {
    const n = nMeasures(); if (!n) return;
    const { from, to } = windowFor(m);
    if (S.activeView && S.activeView.from === from && S.activeView.to === to) return;
    const seq = ++renderSeq;
    const v = getView(from, to);
    const ok = await v.ready;
    if (seq !== renderSeq || !ok || isScroll()) return;   // 期間又換了頁（或換成捲動模式）
    if (S.activeView && S.activeView !== v) { S.activeView.osmd.cursor?.hide(); S.activeView.div.classList.remove("active"); }
    v.div.classList.add("active");
    S.activeView = v; S.osmd = v.osmd;
    S.win = { from, to };
    S.cursorStep = -1;
    const cm = measureOfStep(S.pos);
    if (S.ready && cm >= from && cm <= to) placeCursor(S.pos); else S.osmd.cursor?.hide();
    setWinLabel(from, to);
    drawOverlay();
    prerender();
  }
  function setWinLabel(from, to) {
    const n = nMeasures(), a = sectionOf(from), b = sectionOf(to), name = (i) => S.sections[i].title.split("　")[0];
    const el = $("winLabel");
    el.textContent = S.sections.length > 1
      ? `${name(a)} ${localMeasure(from)}–${b !== a ? name(b) + " " : ""}${localMeasure(to)}`
      : `${from + 1}–${to + 1}`;
    el.title = S.sections.length > 1 ? `全曲第 ${from + 1}–${to + 1} 小節，共 ${n} 小節` : `共 ${n} 小節`;
    $("sectionSelect").value = String(a);
  }
  $("sectionSelect").addEventListener("change", (e) => { const sc = S.sections[+e.target.value]; if (sc) gotoMeasure(sc.start); e.target.blur(); });
  // 背景預先排版：之後幾頁＋前一頁。暫停時多排幾頁；播放中只補下一兩頁，而且每頁之間讓出主執行緒
  let prerenderBusy = false;
  function prerenderTarget() {
    if (S.win.from < 0) return null;
    const n = nMeasures(), cands = [], ahead = S.playing ? 2 : 4;
    let f = S.win.to + 1;
    for (let i = 0; i < ahead && f < n; i++) { const w = windowFor(f); cands.push(w); f = w.to + 1; }
    if (S.win.from > 0) cands.push(windowFor(S.win.from - 1));
    return cands.find((w) => { const v = S.pool.get(w.from); return !v || v.to !== w.to; }) || null;
  }
  async function prerender() {
    if (prerenderBusy) return;
    prerenderBusy = true;
    try {
      for (;;) {
        const w = prerenderTarget(); if (!w) break;
        await new Promise((r) => (window.requestIdleCallback && !S.playing ? requestIdleCallback(() => r(), { timeout: 800 }) : setTimeout(r, 150)));
        const again = prerenderTarget(); if (!again || again.from !== w.from) continue;
        await getView(w.from, w.to).ready;
      }
    } finally { prerenderBusy = false; }
  }

  // ---------- 捲動模式：一條連續往下捲的樂譜 ----------
  // 以「整行」為單位切塊，每塊是一個獨立的 OSMD，由上到下接在 #score 裡。只在同一樂章內往下接／往上補（不跨樂章，和翻頁模式一樣）；游標進入倒數第二塊時在下面接一塊；
  // 塊太多時移除離畫面最遠的（在上方時同一幀從 scrollTop 扣掉它的高度）；游標行用 requestAnimationFrame 平滑地保持在畫面上方約三分之一。
  const CHUNK_MAX = 6;
  const wrapEl = $("scoreWrap"), scoreEl = $("score");
  // 直向捲 scrollTop、橫向捲 scrollLeft；下面的邏輯都用這幾個包起來
  const scrollPos = () => (horiz() ? wrapEl.scrollLeft : wrapEl.scrollTop);
  const setScrollPos = (p) => { if (horiz()) wrapEl.scrollLeft = p; else wrapEl.scrollTop = p; };
  const viewport = () => (horiz() ? wrapEl.clientWidth : wrapEl.clientHeight) || 600;
  const scoreOff = () => (horiz() ? scoreEl.offsetLeft : scoreEl.offsetTop);
  const STALE = Symbol("stale");
  const H_GAP_MIN = 11, H_GAP_MAX = 16;   // 橫向聲部間距的上下限（OSMD 單位）：太小音符會擠到隔壁，太大整行放不進畫面
  const H_LINE_MARGIN = 23;               // 一行上下一定會用掉的單位（頁邊、譜表上方的表情記號）：實測 21–23
  function horizStaffGap() {   // 讓整行剛好放得進畫面（不必上下捲）：五部弦樂 16、六部＋鋼琴約 11
    const staves = S.parts.reduce((a, pp) => a + pp.staves.length, 0) || 1;
    if (staves < 2) return H_GAP_MAX;
    // 0.88：同一份譜每塊的高度不一樣（高音的加線、譜表下方的力度記號會把 SVG 撐高），
    // 而橫向排在一起時最高的那塊決定整體高度，所以要留一點餘裕，否則又要上下捲
    const avail = ((wrapEl.clientHeight || 700) * 0.88 - 24) / (zoomNow() * UNIT) - H_LINE_MARGIN;
    return Math.max(H_GAP_MIN, Math.min(H_GAP_MAX, Math.floor((avail - 4 * staves) / (staves - 1))));
  }
  const RENDER_BUDGET = 60;   // 一塊排版希望不超過的毫秒數（播放中追加一塊時畫面會停這麼久）
  function adaptChunkSize(bars, ms) {   // 依實測每小節的排版時間調整之後試排的小節數（密的段落每小節 10–15 ms）
    if (bars < 4 || ms <= 0) return;
    const want = Math.floor(RENDER_BUDGET / (ms / bars));
    const next = want < S.chunkSize ? want : Math.round((S.chunkSize + want) / 2);   // 超過預算立刻縮小，低於預算慢慢放大
    S.chunkSize = Math.max(4, Math.min(S.chunkMax || 24, next));   // 下限 4：八行譜表（弦樂＋鋼琴＋管樂）一行就要 100 ms
  }
  const yieldFrame = () => new Promise((r) => setTimeout(r, 16));   // 把長工作切開，中間讓瀏覽器畫一幀
  let showSeq = 0, showing = null, fillBusy = false, fillAgain = false, hRefTop = null;
  scoreEl.classList.toggle("scroll", isScroll());
  scoreEl.classList.toggle("horiz", horiz());
  wrapEl.classList.toggle("horiz", horiz());

  function resetChunks() {
    S.chunkGen++; showSeq++; showing = null;   // 進行中的排版一律作廢
    for (const v of S.chunks) dropView(v);
    S.chunks = []; hRefTop = null;
    if (isScroll()) { S.win = { from: -1, to: -1 }; S.osmd = null; }
    S.cursorView = null; S.cursorStep = -1;
  }
  const systemsOf = (osmd) => osmd.GraphicSheet.MusicPages.flatMap((p) => p.MusicSystems);
  function systemLastMeasure(v, sys) {
    const ms = sys.StaffLines[0]?.Measures || [], gm = ms[ms.length - 1];
    return gm ? v.from + v.osmd.Sheet.SourceMeasures.indexOf(gm.parentSourceMeasure) : -1;
  }
  function lastLineRatio(v, systems) {   // 最後一行（沒拉滿）的長度 ÷ 完整一行的長度
    const w = (s) => s.StaffLines[0]?.PositionAndShape.Size.width || 0, last = systems[systems.length - 1];
    if (!last) return 0;
    const r = v.osmd.EngravingRules;
    const full = systems.length > 1 ? Math.max(...systems.slice(0, -1).map(w)) : v.div.clientWidth / px() - r.PageLeftMargin - r.PageRightMargin - 8;
    return full > 0 ? w(last) / full : 0;
  }
  async function buildChunk(from, to, gen, dir, attempt = 0, lead = false, lite = false) {   // dir > 0：接在下面；dir < 0：補在上面。回傳排好（還沒放進畫面）的塊
    const stretchAll = dir > 0 && S.chunkMode === "stretch";
    const v = makeView(true, stretchAll && !horiz(), horiz()); v.from = from; v.to = to;
    const stat = { from, to, dir, loadMs: 0, renderMs: 0 }, t0 = performance.now();
    const alive = () => { if (gen !== S.chunkGen || !isScroll()) throw STALE; if (S.playing) scheduler(2.5); };   // 每段重工作之前先把之後 2.5 秒的聲音排好
    const render = () => { const t = performance.now(); v.osmd.render(); stat.renderMs += Math.round(performance.now() - t); };
    try {
      alive();
      const tl = performance.now();
      await v.osmd.load(sliceXml(from, to, lite));
      stat.loadMs = Math.round(performance.now() - tl);
      await yieldFrame(); alive();
      v.osmd.Zoom = zoomNow();
      if (horiz()) {   // 橫向：各塊的聲部間距要一模一樣，否則接縫下面幾行會對不齊
        const r = v.osmd.EngravingRules, gap = horizStaffGap();
        r.BetweenStaffDistance = gap;
        r.StaffDistance = gap;
        r.MinimumStaffLineDistance = gap;
        r.MinSkyBottomDistBetweenStaves = -100;   // 不要依內容再把聲部推開，否則每塊的間距都不一樣
      }
      render();
      adaptChunkSize(to - from + 1, stat.renderMs);
      const sc = S.sections[sectionOf(from)], secEnd = Math.min(sc ? sc.end : Infinity, nMeasures() - 1);
      if (!horiz() && to < secEnd && !stretchAll) {   // 橫向只有一行，不必截行
        const systems = systemsOf(v.osmd);
        if (dir > 0 && systems.length > 1) {   // 最後一行可能只有半行：藏起來，下一塊從那一行的第一小節開始（不必重排）
          const keep = systems[systems.length - 2], cut = systems[systems.length - 1], last = systemLastMeasure(v, keep);
          if (last >= from && last < to) {
            const kl = keep.StaffLines[keep.StaffLines.length - 1], cl = cut.StaffLines[0];
            const keepBottom = kl.PositionAndShape.AbsolutePosition.y + Math.max(kl.StaffHeight || 4, ...(kl.BottomLine || []));
            const cutTop = cl.PositionAndShape.AbsolutePosition.y + Math.min(0, ...(cl.SkyLine || []));
            v.to = last;
            v.clip = Math.ceil(Math.max(keepBottom + 0.5, Math.min(keepBottom + 1, cutTop - 0.5)) * px());   // 速度記號不算在 skyline 裡：緊貼保留那一行的最低點下方切
          }
        } else if (!S.playing && lastLineRatio(v, systems) >= 0.6) {   // 往上補的塊（或整塊只有一行）：最後一行夠長就拉滿（播放中不多排一次）
          await yieldFrame(); alive();
          v.osmd.EngravingRules.StretchLastSystemLine = true;
          render();
        }
      }
      v.osmd.cursor?.hide();
      const gm0 = v.osmd.GraphicSheet.MeasureList[0] && v.osmd.GraphicSheet.MeasureList[0][0];
      const sys0 = systemsOf(v.osmd)[0];
      v.staffTop = sys0 && sys0.StaffLines[0] ? Math.round(sys0.StaffLines[0].PositionAndShape.AbsolutePosition.y * px()) : 0;
      stat.to = v.to; stat.ms = Math.round(performance.now() - t0); stat.at = Math.round(performance.now());
      S.chunkStats.push(stat); if (S.chunkStats.length > 200) S.chunkStats.shift();
      return v;
    } catch (e) {
      dropView(v);
      if (e === STALE) return null;
      // OSMD 的已知問題：切片第一小節裡的力度記號有時會讓排版找不到左邊的音符而出錯。
      // 先拿掉第一小節的力度記號重試（只影響顯示，播放用的是時間軸）；還不行再少一小節
      if (!lite) { console.warn(`[scroll] 第 ${from + 1}–${to + 1} 小節排版失敗，拿掉第一小節的力度記號重試`, e); return buildChunk(from, to, gen, dir, attempt, lead, true); }
      if (to - from >= 3 && attempt < 3) {   // OSMD 偶爾解析某些切片會出錯：少一小節再試（往上補的塊從前面少，才接得上）
        console.warn(`[scroll] 第 ${from + 1}–${to + 1} 小節排版失敗，縮短重試`, e);
        return dir < 0 ? buildChunk(from + 1, to, gen, dir, attempt + 1, lead, true) : buildChunk(from, to - 1, gen, dir, attempt + 1, lead, true);
      }
      throw e;
    }
  }
  function shiftScroll(st, delta) {   // 前面加入／移除了內容：同一幀補償捲動位置，畫面不跳（Safari 沒有 overflow-anchor）
    if (!delta) return;
    setScrollPos(st + delta);
    if (S.follow.y !== null) S.follow.y += delta;
    S.follow.dirty = true;
  }
  function insertChunk(v, dir) {
    const first = S.chunks[0], st = scrollPos(), before = first && dir < 0 ? viewOff(first) : 0;
    if (dir < 0 && first) { scoreEl.insertBefore(v.div, first.div); S.chunks.unshift(v); }
    else { scoreEl.appendChild(v.div); S.chunks.push(v); }
    if (horiz()) {   // 橫向：把每塊上下挪一下，讓譜線接得起來
      if (hRefTop === null) hRefTop = v.staffTop || 0;
      v.div.style.marginTop = ((hRefTop || 0) - (v.staffTop || 0)) + "px";
      v.div.style.marginLeft = "";
      v.div.style.clipPath = "";
    }
    if (v.clip) { v.div.style.height = v.clip + "px"; v.div.classList.add("cut"); }
    v.div.classList.toggle("section-start", v.from > 0 && S.sections.length > 1 && S.sections.some((sc) => sc.start === v.from));
    v.div.classList.remove("rendering");
    if (dir < 0 && first) shiftScroll(st, viewOff(first) - before);
  }
  function removeChunk(v) {
    const i = S.chunks.indexOf(v); if (i < 0) return;
    const next = S.chunks[i + 1], st = scrollPos(), before = next ? viewOff(next) : 0;
    S.chunks.splice(i, 1); dropView(v);
    if (next) shiftScroll(st, viewOff(next) - before);
    if (S.cursorView === v) { S.cursorView = null; S.cursorStep = -1; }
  }
  function chunksChanged() {
    const cs = S.chunks;
    S.win = cs.length ? { from: cs[0].from, to: cs[cs.length - 1].to } : { from: -1, to: -1 };
    S.osmd = (cs.includes(S.cursorView) ? S.cursorView : cs[0])?.osmd || null;
    S.follow.dirty = true;
    drawOverlay(); updateScrollLabel();
  }
  function scrollAnchor(m) {   // 重排前記下包含 m 的塊從哪一小節開始、在畫面上的位置
    const v = viewOf(m);
    return v ? { from: v.from, to: v.to, y: scoreOff() + viewOff(v) - scrollPos() } : null;
  }
  async function scrollShow(m, anchor) {   // 讓第 m 小節出現在畫面上：已經排好就不動，否則從 m（或 anchor 那一塊的開頭）重新排
    const n = nMeasures(); if (!n) return;
    m = Math.max(0, Math.min(m, n - 1));
    if (viewOf(m)) return;
    if (showing && m >= showing.from && m <= showing.to) return showing.promise;
    const last = S.chunks[S.chunks.length - 1];
    if (S.playing && last && m > last.to && m <= last.to + S.chunkSize && sectionOf(m) === sectionOf(last.to)) { fillChunks(); return; }   // 同一樂章的下一塊：照常接在下面，不整個重排；進到下一樂章則整個換掉
    if (anchor && !(anchor.from <= m && m <= anchor.to && sectionOf(anchor.from) === sectionOf(m))) anchor = null;
    const from = anchor ? anchor.from : m, sc = S.sections[sectionOf(m)];
    const to = Math.min(from + S.chunkSize - 1, sc ? sc.end : n - 1, n - 1);
    const seq = ++showSeq, gen = ++S.chunkGen;
    S.layoutWidth = horiz() ? wrapEl.clientHeight : scoreEl.clientWidth;
    const p = (async () => {
      const v = await buildChunk(from, to, gen, 1, 0, true);   // 錨點那一塊是一行的開頭，要畫譜號調號
      if (!v) return;
      if (seq !== showSeq) { dropView(v); return; }
      for (const old of S.chunks) dropView(old);   // 新的一塊排好才換掉舊的，畫面不會空白
      S.chunks = []; S.cursorView = null; S.cursorStep = -1;
      insertChunk(v, 1);
      if (S.ready && viewOf(measureOfStep(S.pos))) placeCursor(S.pos);
      chunksChanged();
      const f = S.follow; f.y = null; f.dirty = true;
      if (anchor) setScrollPos(scoreOff() + viewOff(v) - anchor.y);
      else { f.paused = false; setScrollPos(followTarget(m) ?? 0); }
      updateScrollLabel();
    })();
    showing = { from, to, promise: p };
    try { await p; } finally { if (seq === showSeq) showing = null; }
    fillChunks();
  }
  function wantChunk() {   // 需要再排一塊嗎？{dir: 1} 接在下面、{dir: -1} 補在上面（只到這個樂章的頭尾為止）
    const cs = S.chunks; if (!cs.length || showing || !isScroll()) return null;
    const n = nMeasures(), H = viewport(), top = scrollPos() - scoreOff();
    const first = cs[0], last = cs[cs.length - 1], bottom = viewOff(last) + viewSize(last);
    const far = (v) => viewOff(v) + viewSize(v) < top - H || viewOff(v) > top + 2.5 * H;
    const secOfLast = S.sections[sectionOf(last.to)], secOfFirst = S.sections[sectionOf(first.from)];
    if (last.to < Math.min(secOfLast ? secOfLast.end : n - 1, n - 1)) {
      if (S.playing) {
        const cm = measureOfStep(S.pos), ci = cs.findIndex((v) => cm <= v.to);
        if (ci < 0) return { dir: 1 };   // 游標已經走出最後一塊
        const loopInside = S.loopOn && S.loop && cm >= S.loop.start && cm <= S.loop.end && S.loop.end <= last.to;   // 循環區段都排好了就不必再往下
        if (!loopInside && (cs.length - 1 - ci < 2 || (!S.follow.paused && bottom < top + 2 * H))) return { dir: 1 };
      } else if (bottom < top + 2.5 * H && (cs.length < CHUNK_MAX || far(first))) return { dir: 1 };
    }
    if (!S.playing && first.from > (secOfFirst ? secOfFirst.start : 0) && viewOff(first) > top - H && (cs.length < CHUNK_MAX || far(last))) return { dir: -1 };
    return null;
  }
  function recycleChunks() {   // 塊太多：移除離畫面最遠、而且不需要的那一端
    while (S.chunks.length > CHUNK_MAX) {
      const cs = S.chunks, H = viewport(), top = scrollPos() - scoreOff();
      const first = cs[0], last = cs[cs.length - 1], cm = measureOfStep(S.pos);
      const bottomOf = (v) => viewOff(v) + viewSize(v);
      const far = (v) => bottomOf(v) < top - H || viewOff(v) > top + 2.5 * H;
      const dist = (v) => (v === first ? top - bottomOf(first) : viewOff(last) - (top + H));
      const inView = (v) => bottomOf(v) > top && viewOff(v) < top + H;
      // 游標所在與之後的塊、畫面上看得到的塊留著。沒在播放時，游標那塊離畫面很遠就可以丟
      // （否則使用者往右捲、游標留在最前面那塊時，新排好的塊會被立刻丟掉，永遠接不下去）
      const keep = (v) => (v === S.cursorView && (S.playing || !far(v))) || (S.playing && v.from > cm) || (inView(v) && cs.length <= CHUNK_MAX + 2);
      const victim = [first, last].filter((v) => !keep(v)).sort((a, b) => (far(b) - far(a)) || (dist(b) - dist(a)))[0];
      if (!victim) break;
      removeChunk(victim);
    }
  }
  async function addChunk(w, gen) {
    const cs = S.chunks, n = nMeasures();
    if (!cs.length) return;
    let from, to;
    if (w.dir > 0) { from = cs[cs.length - 1].to + 1; if (from >= n || sectionOf(from) !== sectionOf(from - 1)) return; const sc = S.sections[sectionOf(from)]; to = Math.min(from + S.chunkSize - 1, sc ? sc.end : n - 1, n - 1); }
    else { to = cs[0].from - 1; if (to < 0 || sectionOf(to) !== sectionOf(to + 1)) return; const sc = S.sections[sectionOf(to)]; from = Math.max(sc ? sc.start : 0, to - S.chunkSize + 1); }
    const lead = w.dir < 0 || S.sections.some((sc) => sc.start === from);   // 補在前面的、或樂章開頭：要畫譜號調號
    const v = await buildChunk(from, to, gen, w.dir, 0, lead);
    if (!v) return;
    const now = S.chunks, ok = now.length && (w.dir > 0 ? now[now.length - 1].to + 1 === from : now[0].from - 1 === to);
    if (!ok || gen !== S.chunkGen) { dropView(v); return; }
    insertChunk(v, w.dir);
    recycleChunks();
    const cv = viewOf(measureOfStep(S.pos));
    if (S.ready && cv && cv !== S.cursorView) placeCursor(S.pos);   // 游標剛好走進新的一塊
    chunksChanged();
  }
  async function fillChunks() {
    if (!isScroll()) return;
    if (fillBusy) { fillAgain = true; return; }
    fillBusy = true;
    try {
      for (let guard = 0; guard < 24; guard++) {
        if (!wantChunk()) break;
        if (!S.playing) await new Promise((r) => (window.requestIdleCallback ? requestIdleCallback(() => r(), { timeout: 400 }) : setTimeout(r, 50)));   // 暫停時利用空檔多排幾塊
        const w = wantChunk(); if (!w) break;
        try { await addChunk(w, S.chunkGen); } catch (e) { console.error("[scroll] 追加樂譜失敗", e); break; }
      }
    } finally {
      fillBusy = false;
      if (fillAgain) { fillAgain = false; setTimeout(fillChunks, 0); }
    }
  }
  function followTarget(m) {   // 直向：行停在畫面上方三分之一；橫向：小節停在畫面左邊三分之一
    const b = measureBox(m); if (!b) return null;
    const W = viewport();
    if (horiz()) return Math.max(0, Math.round(scoreOff() + b.x - Math.max(12, Math.min(W / 3, W - b.w - 12))));
    return Math.max(0, Math.round(scoreOff() + b.y - Math.max(12, Math.min(W / 3, W - b.h - 12))));
  }
  function horizTarget(m, p) {   // 橫向的四種跟隨方式；p = 這一小節已經走了幾成
    const b = measureBox(m); if (!b) return null;
    const W = viewport(), pos = scrollPos(), left = scoreOff() + b.x, right = left + b.w;
    if (left < pos - 4 || right > pos + W + 4) {   // 這一小節根本不在畫面上（跳小節、循環跳回、跳過長休止）：先拉回來
      if (S.hFollow === "jump" || S.hFollow === "half") return Math.max(0, Math.round(left - 12));
    }
    switch (S.hFollow) {
      case "smooth": {                                   // 游標固定在同一個 x，樂譜連續往左流
        const t0 = followTarget(m), b1 = measureBox(m + 1);
        const t1 = b1 ? Math.max(0, Math.round(scoreOff() + b1.x - Math.max(12, Math.min(W / 3, W - b1.w - 12)))) : t0;
        return t0 + (t1 - t0) * p;
      }
      case "jump":                                       // 走到畫面最右邊才跳，讓這一小節變成最左邊
        return right > pos + W - 8 ? Math.max(0, Math.round(left - 12)) : pos;
      case "half":                                       // 過了三分之二就往左移半個畫面
        return left > pos + W * 2 / 3 ? Math.max(0, Math.round(pos + W / 2)) : pos;
      default:                                           // step：小節釘在左邊三分之一，換小節才滑動
        return followTarget(m);
    }
  }
  function followFrame() {   // 每幀往目標 scrollTop 靠近；在一行的最後一小節裡就開始往下一行移。回傳 true = 還在移動
    const f = S.follow;
    if (!isScroll() || f.paused) { f.t = 0; return false; }
    const now = performance.now(), dt = f.t ? Math.min(100, now - f.t) : 16; f.t = now;
    const m = measureOfStep(S.pos);
    if (m !== f.m || f.dirty) {
      f.m = m; f.dirty = false; f.base = followTarget(m); f.next = null;
      const loopEnd = S.loopOn && S.loop && m === S.loop.end, skipNext = S.skipSolo && S.skips.some((k) => k.start === m + 1);
      if (f.base !== null && !loopEnd && !skipNext && m + 1 < nMeasures()) {
        const b0 = measureBox(m), b1 = measureBox(m + 1);
        if (b0 && b1 && b1.y > b0.y + 1) { f.next = followTarget(m + 1); f.cap = Math.max(f.base, Math.round(scoreEl.offsetTop + b0.y + b0.h - 48)); }
      }
    }
    if (f.base === null) return false;
    let target = f.base;
    if (horiz()) {
      const a = S.measureStartBeat[m], b2 = m + 1 < S.measureStartBeat.length ? S.measureStartBeat[m + 1] : S.totalBeats;
      const p = S.playing ? Math.max(0, Math.min(1, (S.beatNow - a) / ((b2 - a) || 1))) : 0;
      const t = horizTarget(m, p);
      if (t === null) return false;
      f.target = t;
      const st0 = scrollPos();
      let y0 = f.y !== null && Math.abs(st0 - f.y) <= 2 ? f.y : st0;
      const d0 = t - y0;
      if (Math.abs(d0) < 0.5) { f.y = t; return false; }
      const snap = S.hFollow === "jump" || Math.abs(d0) > 2 * viewport();
      y0 = snap ? t : y0 + d0 * (1 - Math.exp(-dt / (S.hFollow === "smooth" ? 90 : 150)));
      f.y = y0; setScrollPos(y0);
      return true;
    }
    if (S.playing && f.next !== null) {
      const a = S.measureStartBeat[m], b = m + 1 < S.measureStartBeat.length ? S.measureStartBeat[m + 1] : S.totalBeats;
      const p = Math.max(0, Math.min(1, (S.beatNow - a) / ((b - a) || 1)));
      target = Math.min(target + (f.next - f.base) * p * p * (3 - 2 * p), f.cap);   // 行比畫面高時，目前這一行至少留一截在畫面上
    }
    f.target = target;
    const st = scrollPos();
    let y = f.y !== null && Math.abs(st - f.y) <= 2 ? f.y : st;
    const d = target - y;
    if (Math.abs(d) < 0.5) { if (Math.abs(st - target) >= 1) setScrollPos(target); f.y = target; return false; }
    y = Math.abs(d) > 1.5 * viewport() ? target : y + d * (1 - Math.exp(-dt / 180));   // 很遠（循環跳回、跳過長休止）就直接跳
    f.y = y; setScrollPos(y);
    return true;
  }
  function resumeFollow() {   // 按播放或跳小節：恢復自動跟隨；沒在播放時自己跑幾幀把畫面捲到位
    if (!isScroll()) return;
    const f = S.follow; f.paused = false; f.dirty = true; f.frames = 0;
    if (f.raf) return;
    const loop = () => { f.raf = null; if (S.playing || f.paused || !isScroll()) return; if (followFrame() && ++f.frames < 120) f.raf = requestAnimationFrame(loop); };
    f.raf = requestAnimationFrame(loop);
  }
  const pauseFollow = () => { if (isScroll()) S.follow.paused = true; };   // 使用者自己捲：暫停跟隨，按播放或跳小節才恢復
  wrapEl.addEventListener("wheel", pauseFollow, { passive: true });
  wrapEl.addEventListener("touchmove", pauseFollow, { passive: true });
  wrapEl.addEventListener("pointerdown", (e) => { if (e.target === wrapEl && (e.offsetX >= wrapEl.clientWidth || e.offsetY >= wrapEl.clientHeight)) pauseFollow(); });   // 拖捲軸
  // 沒在播放時捲到這個樂章的最後：再往右（直向是往下）推一下，就接著顯示下一個樂章。
  // 剛捲到底時不換（最後幾小節要看得到、觸控板的慣性也會多推一段），停在底部一下之後再推才換。
  const maxScroll = () => (horiz() ? wrapEl.scrollWidth - wrapEl.clientWidth : wrapEl.scrollHeight - wrapEl.clientHeight);
  function nextSectionStart() {   // 已經捲到這個樂章的尾巴、而且後面還有樂章：回傳下一樂章的第一小節，否則 -1
    const last = S.chunks[S.chunks.length - 1]; if (!last || showing || S.playing) return -1;
    const sc = S.sections[sectionOf(last.to)];
    if (!sc || last.to < sc.end || sc.end + 1 >= nMeasures()) return -1;
    return scrollPos() >= maxScroll() - 2 ? sc.end + 1 : -1;
  }
  const PUSH_WAIT = 600, PUSH_NEED = 300;
  let atEndSince = 0, pushed = 0;
  wrapEl.addEventListener("wheel", (e) => {
    if (!isScroll()) return;
    const d = (horiz() && Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * (e.deltaMode ? 40 : 1);
    const m = d > 0 ? nextSectionStart() : -1;
    if (m < 0) { atEndSince = 0; pushed = 0; return; }
    const now = performance.now();
    if (!atEndSince) atEndSince = now;
    if (now - atEndSince < PUSH_WAIT) return;
    if ((pushed += d) > PUSH_NEED) { atEndSince = 0; pushed = 0; scrollShow(m); }
  }, { passive: true });
  let scrollRaf = 0;
  wrapEl.addEventListener("scroll", () => {
    if (!isScroll() || scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0; updateScrollLabel(); if (!S.playing) fillChunks();
      if (nextSectionStart() < 0) { atEndSince = 0; pushed = 0; } else if (!atEndSince) atEndSince = performance.now();
    });
  }, { passive: true });
  function updateScrollLabel() {   // 「顯示」標籤：畫面上看得到的塊的範圍
    if (!isScroll() || !S.chunks.length) return;
    const top = scrollPos() - scoreOff(), H = viewport();
    const vis = S.chunks.filter((v) => viewOff(v) + viewSize(v) > top && viewOff(v) < top + H);
    const a = vis[0] || S.chunks[0], b = vis[vis.length - 1] || a;
    setWinLabel(a.from, b.to);
  }
  function pageStep(dir) {
    if (!isScroll()) { renderWindow(dir < 0 ? S.win.from - 1 : S.win.to + 1); return; }   // pageStart 會算回上一個視窗（或上一樂章的最後一個）
    S.follow.paused = true;
    const next = dir > 0 ? nextSectionStart() : -1;
    if (next >= 0) { scrollShow(next); return; }   // 已經在樂章的尾巴：翻到下一個樂章
    if (horiz()) wrapEl.scrollBy({ left: dir * wrapEl.clientWidth * 0.85, behavior: "smooth" });
    else wrapEl.scrollBy({ top: dir * wrapEl.clientHeight * 0.85, behavior: "smooth" });
  }
  function setDisplay(mode) {
    if (!["page", "scroll", "horizontal"].includes(mode)) mode = "scroll";
    localStorage.setItem("displayMode", mode); $("displayMode").value = mode;
    if (mode === S.display) return;
    const m = S.ready ? measureOfStep(S.pos) : Math.max(0, S.win.from);
    resetDisplay();
    S.display = mode;
    scoreEl.classList.toggle("scroll", isScroll());
    scoreEl.classList.toggle("horiz", horiz());
    wrapEl.classList.toggle("horiz", horiz());
    wrapEl.scrollTop = wrapEl.scrollLeft = 0; $("overlay").innerHTML = "";
    if (nMeasures()) showMeasure(m).then(() => { setCursor(S.pos); drawOverlay(); });
  }
  $("displayMode").value = S.display;
  $("displayMode").addEventListener("change", (e) => { setDisplay(e.target.value); e.target.blur(); });
  $("hFollow").value = S.hFollow;
  $("hFollow").addEventListener("change", (e) => {   // 實驗：橫向的四種跟隨方式
    S.hFollow = e.target.value; localStorage.setItem("hFollow", S.hFollow);
    S.follow.dirty = true; S.follow.y = null; resumeFollow(); e.target.blur();
  });

  function ensureVisible(m) { if (viewOf(m)) return true; showMeasure(m); return false; }
  $("gotoBar").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const txt = e.target.value.trim(); let m = -1; let mm;
    if ((mm = /^#\s*(\d+)$/.exec(txt))) m = +mm[1] - 1;                                   // 全曲小節號
    else if ((mm = /^(\d+)\s*[:：]\s*(\d+)$/.exec(txt))) { const sc = S.sections[+mm[1] - 1]; if (sc) m = sc.start + (+mm[2]) - 1; }   // 樂章:小節
    else if ((mm = /^(\d+)$/.exec(txt))) { const sc = S.sections[sectionOf(measureOfStep(S.pos))]; m = (sc ? sc.start : 0) + (+mm[1]) - 1; }  // 本樂章的小節
    if (m < 0 || m >= nMeasures()) { e.target.classList.add("bad"); setTimeout(() => e.target.classList.remove("bad"), 600); return; }
    gotoMeasure(m); e.target.blur();
  });
  $("pagePrev").addEventListener("click", () => pageStep(-1));
  $("pageNext").addEventListener("click", () => pageStep(1));

  function buildParts() {
    const old = new Map(S.parts.map((p) => [p.xmlId, p]));
    const names = new Map(Array.from(S.xmlDoc.querySelectorAll("score-partwise > part-list > score-part")).map((sp) => [sp.getAttribute("id"), (sp.querySelector("part-name")?.textContent || "").trim()]));
    let staffIdx = 0;
    S.parts = xmlParts().map((part, i) => {
      const xmlId = part.getAttribute("id") || `P${i + 1}`;
      const name = S.workInfo?.parts.find((p) => p.xmlId === xmlId)?.part || names.get(xmlId) || `聲部 ${i + 1}`;
      const nStaves = Math.max(1, +(part.querySelector("measure > attributes > staves")?.textContent || 1));
      const staves = Array.from({ length: nStaves }, (_, k) => staffIdx + k); staffIdx += nStaves;
      const prev = old.get(xmlId);
      return { index: i, xmlId, name, mute: prev ? prev.mute : false, preset: presetFor(name), staves };
    });
    if (S.practice >= S.parts.length) S.practice = 0;
  }
  function renderPartsUI() {
    const g = $("partsGroup"); g.innerHTML = "";
    if (S.parts.length === 0) return;
    const lab = document.createElement("span"); lab.className = "muted"; lab.textContent = S.parts.length > 1 ? "聲部：" : ""; g.appendChild(lab);
    S.parts.forEach((p) => {
      const row = document.createElement("span"); row.className = "partrow" + (p.index === S.practice ? " practice" : "");
      row.title = "勾選 = 播放此聲部；點名稱 = 設為練習聲部";
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !p.mute;
      cb.addEventListener("change", () => { p.mute = !cb.checked; scheduleSave(); });
      const nm = document.createElement("span"); nm.textContent = p.name; nm.style.cursor = "pointer";
      nm.addEventListener("click", () => { S.practice = p.index; renderPartsUI(); drawOverlay(); checkBeats(); scheduleSave(); });
      row.append(cb, nm); g.appendChild(row);
    });
    if (S.parts.length > 1) {
      const l = document.createElement("label"); l.innerHTML = `練習聲部音量 <input id="myVolume" type="range" min="0" max="1" step="0.05" value="${S.myVolume}" title="練習聲部（自己的聲部）音量，0 = 只聽其他聲部">`;
      g.appendChild(l);
      $("myVolume").addEventListener("input", (e) => { S.myVolume = +e.target.value; scheduleSave(); });
    }
  }

  function analyzeOsmd(sheet) {   // 用 OSMD 解析結果算時間軸（格式和伺服器的 timeline.json 相同）
    sheet.SelectionStart = new OSMD.Fraction(0, 1);
    sheet.SelectionEnd = sheet.SheetEndTimestamp;
    const it = sheet.MusicPartManager.getIterator(new OSMD.Fraction(0, 1));
    const instruments = sheet.Instruments, measures = sheet.SourceMeasures, n = measures.length;
    // 移調樂器（例如低音提琴記譜高八度）：OSMD 的 halfTone 是記譜音高，播放要用實際音高
    const chrom = xmlParts().map((part) => { const tr = part.querySelector("measure > attributes > transpose"); return tr ? +(tr.querySelector("chromatic")?.textContent || 0) + 12 * +(tr.querySelector("octave-change")?.textContent || 0) : 0; });
    const steps = [], firstStep = new Array(n).fill(null);
    let guard = 0;
    while (!it.EndReached && guard++ < 400000) {
      const src = it.CurrentSourceTimestamp, m = it.CurrentMeasureIndex, notes = [];
      for (const ve of it.CurrentVoiceEntries || []) {
        if (ve.IsGrace) continue;                       // 倚音不發聲（和伺服器一致）
        const part = Math.max(0, instruments.indexOf(ve.ParentVoice.Parent));
        for (const nt of ve.Notes) {
          if (nt.isRest() || nt.IsCueNote) continue;
          if (nt.NoteTie && nt.NoteTie.StartNote !== nt) continue;
          let len = nt.Length.RealValue;
          if (nt.NoteTie && nt.NoteTie.StartNote === nt) len = nt.NoteTie.Notes.reduce((acc, x) => acc + x.Length.RealValue, 0);
          notes.push([nt.halfTone + 12 + (chrom[part] || 0), len * 4, part]);
        }
      }
      if (m >= 0 && m < n && firstStep[m] === null) firstStep[m] = steps.length;
      steps.push([it.CurrentEnrolledTimestamp.RealValue * 4, m, src.GetExpandedNumerator(), src.Denominator, notes]);
      it.moveToNext();
    }
    let next = steps.length;
    for (let m = n - 1; m >= 0; m--) { if (firstStep[m] === null) firstStep[m] = next; else next = firstStep[m]; }
    const start = measures.map((mm) => mm.AbsoluteTimestamp.RealValue * 4);
    let total = n ? start[n - 1] + measures[n - 1].Duration.RealValue * 4 : 0;
    for (const st of steps) for (const x of st[4]) total = Math.max(total, st[0] + x[1]);
    let cur = 0;
    const tempo = measures.map((mm) => { if (mm.TempoInBPM > 0) cur = mm.TempoInBPM; return cur; });
    return {
      v: 2, nMeasures: n, totalBeats: total, hasTempo: tempo.some((t) => t > 0), tempo: tempo.map((t) => t || 100),
      timeSig: measures.map((mm) => (mm.ActiveTimeSignature ? [mm.ActiveTimeSignature.Numerator, mm.ActiveTimeSignature.Denominator] : [4, 4])),
      measureStartBeat: start, measureTs: measures.map((mm) => [mm.AbsoluteTimestamp.GetExpandedNumerator(), mm.AbsoluteTimestamp.Denominator]),
      measureFirstStep: firstStep, steps,
    };
  }
  function analyzeFull() { restoreTimeline(analyzeOsmd(S.full.Sheet)); }
  function timelineDiff(a, b) {   // 只比對音符與時間（速度標記不影響游標位置）
    const out = [];
    if (a.nMeasures !== b.nMeasures) out.push(`小節數 ${a.nMeasures} ≠ ${b.nMeasures}`);
    if (a.steps.length !== b.steps.length) out.push(`時間點數 ${a.steps.length} ≠ ${b.steps.length}`);
    for (let i = 0; i < Math.min(a.measureStartBeat.length, b.measureStartBeat.length); i++) {
      if (Math.abs(a.measureStartBeat[i] - b.measureStartBeat[i]) > 1e-6) { out.push(`第 ${i + 1} 小節起點不同`); break; }
    }
    const key = (st) => st[4].map((x) => `${x[2]}:${x[0]}:${(+x[1]).toFixed(4)}`).sort().join(",");
    for (let i = 0; i < Math.min(a.steps.length, b.steps.length); i++) {
      const x = a.steps[i], y = b.steps[i];
      if (Math.abs(x[0] - y[0]) > 1e-6 || x[1] !== y[1] || key(x) !== key(y)) { out.push(`第 ${x[1] + 1} 小節附近的音符不同`); break; }
    }
    return { same: out.length === 0, detail: out };
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitQuiet() {   // 等到沒在播放、也沒在預排版，而且瀏覽器有空
    for (;;) {
      while (S.playing || prerenderBusy || fillBusy) await sleep(800);
      await new Promise((r) => (window.requestIdleCallback ? requestIdleCallback(() => r(), { timeout: 3000 }) : setTimeout(r, 1000)));
      if (!S.playing && !prerenderBusy && !fillBusy) return;
    }
  }
  async function verifyTimeline(serverTl, stamp, slug) {
    const xml = S.xmlText;
    await waitQuiet();
    if (S.slug !== slug || S.xmlText !== xml) return;          // 期間換了作品或改了譜
    const t0 = performance.now();
    if (!S.full) { const hidden = document.createElement("div"); hidden.style.display = "none"; document.body.appendChild(hidden); S.full = new OSMD.OpenSheetMusicDisplay(hidden, { autoResize: false, backend: "svg" }); }
    await S.full.load(xml);
    if (S.slug !== slug || S.xmlText !== xml) return;
    const mine = analyzeOsmd(S.full.Sheet);
    const diff = timelineDiff(serverTl, mine);
    S.loadTimes = { ...(S.loadTimes || {}), verifyMs: Math.round(performance.now() - t0), verified: diff.same ? "same" : diff.detail.join("；") };
    const chosen = diff.same ? serverTl : mine;
    if (!diff.same) {
      while (S.playing) await sleep(500);                       // 播放中不換時間軸，停下來再換
      if (S.slug !== slug || S.xmlText !== xml) return;
      const m = measureOfStep(S.pos);
      restoreTimeline(mine); rebuildTimeMap(); buildTicks(); computeSkips();
      S.cursorStep = -1; setCursor(S.measureFirstStep[m] ?? 0); drawOverlay();
      console.info("[timeline] 伺服器時間軸和 OSMD 不同，已改用 OSMD：", diff.detail);
    }
    try { await fetch(`/api/works/${slug}/timeline.json?stamp=${encodeURIComponent(stamp)}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(chosen) }); } catch (e) { /* 下次再核對 */ }
  }
  function buildSections() {
    const xmlM = xmlMeasures(0);
    const starts = [0];
    xmlM.forEach((m, i) => { if (i > 0 && /^movement-/.test(m.getAttribute("id") || "")) starts.push(i); });
    S.sections = starts.map((st, i) => {
      const end = (starts[i + 1] ?? xmlM.length) - 1;
      const words = xmlM[st] ? Array.from(xmlM[st].querySelectorAll(":scope > direction direction-type > words")).map((w) => w.textContent.trim()).filter((t) => t && t.length < 40) : [];
      return { start: st, end, title: `第 ${i + 1} 樂章` + (words.length ? `　${words[0]}` : "") };
    });
    const sel = $("sectionSelect"); sel.innerHTML = "";
    // 選項只放樂章名稱（小節數放 title）：工具列寬度有限，名稱要完整顯示
    S.sections.forEach((sc, i) => { const o = document.createElement("option"); o.value = i; o.textContent = sc.title; o.title = `${sc.end - sc.start + 1} 小節`; sel.appendChild(o); });
    sel.parentElement.classList.toggle("hidden", S.sections.length < 2);
  }
  const sectionOf = (m) => { let i = 0; for (let k = 0; k < S.sections.length; k++) if (S.sections[k].start <= m) i = k; return i; };
  const localMeasure = (m) => m - (S.sections[sectionOf(m)]?.start || 0) + 1;
  function buildTicks() {
    const ticks = [], n = S.nMeasures;
    for (let m = 0; m < n; m++) {
      if (!inClickRange(m)) continue;
      const [num, den] = S.timeSig[m] || [4, 4], beatLen = 4 / den;
      const mEnd = m + 1 < n ? S.measureStartBeat[m + 1] : S.totalBeats;
      for (let b = 0; b < num && S.measureStartBeat[m] + b * beatLen < mEnd - 1e-6; b++) ticks.push({ beat: S.measureStartBeat[m] + b * beatLen, accent: b === 0 });
    }
    S.ticks = ticks;
  }
  const isSoloPart = (name) => /piano|pianoforte|klavier|solo|harp|cembalo|harpsichord|organ|鋼琴|獨奏/i.test(name || "");
  function computeSkips() {
    S.skips = [];
    if (!S.skipSolo || !S.parts.length) { renderSkipInfo(); return; }
    const parts = xmlParts();
    const consider = S.parts.map((p) => p.index).filter((i) => !isSoloPart(S.parts[i].name));
    if (!consider.length) { renderSkipInfo(); return; }
    const n = nMeasures();
    const silent = new Array(n).fill(true);
    for (const pi of consider) {
      xmlMeasures(pi).forEach((m, i) => { if (i < n && m.querySelector(":scope > note > pitch")) silent[i] = false; });
    }
    const minLen = Math.max(1, S.skipMin | 0), keep = Math.max(0, S.skipKeep | 0);
    let m = 0;
    while (m < n) {
      if (!silent[m]) { m++; continue; }
      let e = m; while (e + 1 < n && silent[e + 1]) e++;
      if (e - m + 1 > minLen && e - keep >= m) S.skips.push({ start: m, end: e - keep, runEnd: e });
      m = e + 1;
    }
    renderSkipInfo();
  }
  function renderSkipInfo() {
    const bars = S.skips.reduce((a, k) => a + k.end - k.start + 1, 0);
    $("skipInfo").textContent = S.skipSolo ? (S.skips.length ? `　跳過 ${S.skips.length} 段、共 ${bars} 小節（灰色）` : "　沒有符合的段落") : "";
  }
  function nextSkip(afterBeat, beforeBeat) { // 從 afterBeat 往後第一個會碰到的跳過段（起點在 (afterBeat, beforeBeat) 之間）
    for (const k of S.skips) {
      const fromBeat = S.measureStartBeat[k.start];
      if (fromBeat > afterBeat + 1e-6 && fromBeat < beforeBeat - 1e-6) return { fromBeat, toStep: S.measureFirstStep[k.end + 1] ?? S.steps.length, toBeat: S.measureStartBeat[k.end + 1] ?? S.totalBeats };
    }
    return null;
  }
  function inClickRange(m) {
    if (S.clickMode === "all") return true;
    if (S.clickMode === "ranges") return S.clickRanges.some(([a, b]) => m >= a && m <= b);
    return false;
  }

  // ---------- 拍數檢查 ----------
  function xmlParts() { return Array.from(S.xmlDoc.querySelectorAll("score-partwise > part")); }
  function xmlMeasures(partIdx) { return Array.from(xmlParts()[partIdx]?.children || []).filter((e) => e.tagName === "measure"); }
  const durOf = (el) => { const d = el.querySelector(":scope > duration"); return d ? +d.textContent : 0; };
  function measureFill(mEl) { // 回傳 {max, hasNotes}：max = 此小節被填到的最遠位置（divisions）
    let pos = 0, last = 0, max = 0, hasNotes = false;
    for (const el of mEl.children) {
      if (el.tagName === "backup") pos -= durOf(el);
      else if (el.tagName === "forward") { pos += durOf(el); max = Math.max(max, pos); }
      else if (el.tagName === "note") {
        hasNotes = true;
        const chord = !!el.querySelector(":scope > chord"), grace = !!el.querySelector(":scope > grace");
        if (chord) continue;
        last = pos; if (!grace) pos += durOf(el);
        max = Math.max(max, pos);
      }
    }
    return { max, hasNotes };
  }
  function checkBeats() {
    const errors = [];
    xmlParts().forEach((part, pi) => {
      let div = 1, beats = 4, beatType = 4;
      Array.from(part.children).filter((e) => e.tagName === "measure").forEach((m, mi) => {
        for (const a of m.querySelectorAll(":scope > attributes")) { // homr 會輸出多個 attributes 區塊
          const d = a.querySelector("divisions"); if (d) div = +d.textContent || div;
          const t = a.querySelector("time"); if (t) { beats = +t.querySelector("beats")?.textContent || beats; beatType = +t.querySelector("beat-type")?.textContent || beatType; }
        }
        const expected = div * 4 * beats / beatType;
        const { max, hasNotes } = measureFill(m);
        const wholeRest = m.querySelector(":scope > note > rest[measure=yes]");
        if (!hasNotes) errors.push({ part: pi, measure: mi, actual: 0, expected, div, empty: true });
        else if (!wholeRest && Math.abs(max - expected) > 0.01) errors.push({ part: pi, measure: mi, actual: max, expected, div, empty: false });
      });
    });
    // 所有聲部在同一小節都同樣「短」→ 是譜上刻意的不完整小節（弱起、反覆前後），不是辨識錯誤
    const nParts = xmlParts().length;
    if (nParts > 1) {
      const byMeasure = new Map();
      for (const e of errors) if (!e.empty) byMeasure.set(e.measure, [...(byMeasure.get(e.measure) || []), e]);
      const agreed = new Set();
      for (const [m, es] of byMeasure) if (es.length === nParts && es.every((e) => Math.abs(e.actual / e.div - es[0].actual / es[0].div) < 1e-6)) agreed.add(m);
      S.errors = errors.filter((e) => e.empty || !agreed.has(e.measure));
    } else S.errors = errors;
    renderErrorBar();
  }
  const fmtBeats = (e) => `${+(e.actual / e.div).toFixed(2)}/${+(e.expected / e.div).toFixed(2)}`;
  function visibleErrors() { return $("errMineOnly").checked ? S.errors.filter((e) => e.part === S.practice) : S.errors; }
  function renderErrorBar() {
    const errs = visibleErrors();
    const bar = $("errorBar");
    if (!S.errors.length) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
    const real = errs.filter((e) => !e.empty).length, empty = errs.length - real;
    $("errorSummary").textContent = `拍數不符 ${real} 小節、空小節 ${empty}（可能的辨識錯誤，紅色標示）`;
  }
  function gotoError(dir) {
    const errs = visibleErrors().map((e) => e.measure).filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b);
    if (!errs.length) return;
    const cur = measureOfStep(S.pos);
    let target = dir > 0 ? errs.find((m) => m > cur) : [...errs].reverse().find((m) => m < cur);
    if (target === undefined) target = dir > 0 ? errs[0] : errs[errs.length - 1];
    gotoMeasure(target);
  }
  $("prevErr").addEventListener("click", () => gotoError(-1));
  $("nextErr").addEventListener("click", () => gotoError(1));
  $("errMineOnly").addEventListener("change", () => { renderErrorBar(); drawOverlay(); });

  // ---------- 游標 ----------
  function placeCursor(k) {
    if (!S.steps.length || !S.ready) return;
    const st = S.steps[k], v = viewOf(st.measure), startTs = v && S.measureTs[v.from];
    if (!startTs) return;
    if (S.cursorView !== v) {   // 換頁／跨到下一塊：舊的游標藏起來，改用這一塊自己的游標
      try { S.cursorView?.osmd.cursor?.hide(); } catch (e) { /* 已清掉 */ }
      S.cursorView = v; S.cursorStep = -1;
    }
    const c = v.osmd.cursor;
    const seek = () => { c.iterator = v.osmd.Sheet.MusicPartManager.getIterator(OSMD.Fraction.minus(st.srcTs, startTs)); c.update(); };   // 時間戳要減掉這頁／塊的起點
    try {
      if (S.cursorStep >= 0 && k >= S.cursorStep && k - S.cursorStep < 40 && st.measure <= v.to) {
        for (let i = S.cursorStep; i < k; i++) c.next();
        if (c.iterator.EndReached) seek();   // 切片裡的時間點比時間軸少：改用時間戳定位
      } else seek();
    } catch (e) { try { seek(); } catch (e2) { c.hide(); S.cursorStep = -1; return; } }
    if (c.hidden) c.show();
    S.cursorStep = k;
    if (isScroll()) S.osmd = v.osmd;
  }
  function setCursor(k) {
    if (!S.steps.length) return;
    k = Math.max(0, Math.min(k, S.steps.length - 1));
    S.pos = k;
    if (ensureVisible(S.steps[k].measure)) placeCursor(k);   // 不在視窗內時 renderWindow 排版完會自己放游標
    updateLoopInputs(); updateSpeedLabel(); updateNowChip();
    if (S.ready && S.steps[k].measure !== S.savedMeasure) scheduleSave();   // 記住練到哪（播放中也會跟著記）
  }
  function updateSpeedLabel() {
    const m = measureOfStep(S.pos);
    const sp = speedAt(m);
    $("bpm").value = Math.round(sp * 100); $("bpmVal").textContent = `${Math.round(sp * 100)}%`;
    for (const c of document.querySelectorAll(".chip[data-speed]")) c.classList.toggle("on", Math.abs(+c.dataset.speed - sp) < 0.005);   // ½ ¾ 原速：標出目前的速度
    $("bpmVal").title = S.sections.length > 1 ? "這個樂章的練習速度（每個樂章分開記）" : "練習速度";
    const where = S.sections.length > 1 ? `${S.sections[sectionOf(m)].title.split("　")[0]} 第 ${localMeasure(m)} 小節 ` : `第 ${localMeasure(m)} 小節 `;
    const el = $("tempoNow");
    el.textContent = `${where}♩=${Math.round(effTempo(m))}`;
    el.title = S.hasTempo ? `譜上 ♩=${Math.round(tempoAt(m))}，練習速度 ${Math.round(speedAt(m) * 100)}%` : "譜上沒有速度標記，以 ♩=100 當原速";
  }
  function gotoMeasure(m) {
    m = Math.max(0, Math.min(m, nMeasures() - 1));
    const k = S.measureFirstStep[m];
    if (S.playing) { stop(false); setCursor(k); play(); } else setCursor(k);
    resumeFollow();   // 捲動模式：跳小節恢復自動跟隨
    scheduleSave();
  }

  // ---------- 播放 ----------
  function play() {
    if (!S.steps.length || !S.ready) return;
    ensureAudio();
    const [ls, endBeat] = loopRange();
    let start = S.pos;
    if (S.loopOn && S.loop && (S.steps[start].beat < S.steps[ls].beat || S.steps[start].beat >= endBeat)) start = ls;
    if (S.steps[start].beat >= endBeat - 1e-6) start = ls;
    if (S.skipSolo) { // 起點落在要跳過的段落裡：直接從保留的提示小節開始
      const m0 = measureOfStep(start);
      const k = S.skips.find((x) => m0 >= x.start && m0 <= x.end);
      if (k && S.measureFirstStep[k.end + 1] !== undefined && S.steps[S.measureFirstStep[k.end + 1]].beat < endBeat) start = S.measureFirstStep[k.end + 1];
    }
    if (isScroll()) { resumeFollow(); ensureVisible(measureOfStep(start)); }   // 按播放恢復自動跟隨
    headOver = false; hideHead();   // 開始練習：工具列立刻收起
    S.playing = true;
    $("btnPlay").textContent = "❚❚ 暫停";
    const now = ctx.currentTime + 0.05;
    let t0 = now;
    if (S.countIn) {
      const m = measureOfStep(start);
      const [num, den] = S.timeSig[m] || [4, 4], beatLen = 4 / den, sb = secPerBeatAt(m);
      for (let b = 0; b < num; b++) tick(now + b * beatLen * sb, b === 0);
      t0 = now + num * beatLen * sb;
    }
    S.segments = [newSegment(t0, start, endBeat)];
    S.finishAt = null;
    S.schedTimer = setInterval(() => scheduler(), 50);
    scheduler();
    S.raf = requestAnimationFrame(animate);
  }
  function newSegment(t0, startStep, endBeat) {
    startStep = Math.min(startStep, S.steps.length - 1);
    const startBeat = S.steps[startStep].beat;
    let nextTick = 0;
    while (nextTick < S.ticks.length && S.ticks[nextTick].beat < startBeat - 1e-6) nextTick++;
    const seg = { t0, startBeat, endBeat, nextStep: startStep, nextTick, skipTo: null };
    const sk = nextSkip(startBeat, endBeat);
    if (sk) { seg.endBeat = sk.fromBeat; seg.skipTo = sk; }   // 先播到跳過段之前，再從跳過段之後接下去
    return seg;
  }
  function scheduler(lookaheadOverride) {
    const lookahead = typeof lookaheadOverride === "number" ? lookaheadOverride : 0.35, now = ctx.currentTime;
    let seg = S.segments[S.segments.length - 1];
    const timeOf = (sg, beat) => sg.t0 + (T(beat) - T(sg.startBeat));
    for (let guard = 0; guard < 5000; guard++) {
      const st = S.steps[seg.nextStep];
      const stepBeat = st && st.beat < seg.endBeat - 1e-6 ? st.beat : Infinity;
      const tk = S.ticks[seg.nextTick];
      const tickBeat = tk && tk.beat < seg.endBeat - 1e-6 ? tk.beat : Infinity;
      if (stepBeat === Infinity && tickBeat === Infinity) {
        const endTime = timeOf(seg, seg.endBeat);
        if (seg.skipTo && (!(S.loopOn && S.loop) || seg.skipTo.toBeat < loopRange()[1] - 1e-6)) {
          seg = newSegment(endTime, seg.skipTo.toStep, loopRange()[1]);
          S.segments.push(seg);
          if (endTime > now + lookahead) return;
          continue;
        }
        if (S.loopOn && S.loop) {
          seg = newSegment(endTime, loopRange()[0], loopRange()[1]);
          S.segments.push(seg);
          if (endTime > now + lookahead) return;
          continue;
        }
        S.finishAt = endTime; return;
      }
      const nextBeat = Math.min(stepBeat, tickBeat), when = timeOf(seg, nextBeat);
      if (when > now + lookahead) return;
      if (tickBeat <= stepBeat) { tick(when, tk.accent); seg.nextTick++; }
      else { const sb = secPerBeatAt(st.measure), hold = S.soundMode === "strings" ? 0.95 : 0.99; for (const n of st.notes) playNote(n, when, Math.max(0.05, n.beats * sb * hold)); seg.nextStep++; }
    }
  }
  function animate() {
    if (!S.playing) return;
    const now = ctx.currentTime;
    while (S.segments.length > 1 && S.segments[1].t0 <= now) S.segments.shift();
    const seg = S.segments[0];
    if (S.finishAt !== null && now >= S.finishAt) { stop(false); return; }  // 播完停在結尾，不跳回起點
    if (now >= seg.t0) {
      S.beatNow = Tinv(T(seg.startBeat) + (now - seg.t0));
      const k = stepAtBeat(S.beatNow);
      if (k !== S.cursorStep) {
        const before = measureOfStep(S.cursorStep);
        setCursor(k);
        if (isScroll() && measureOfStep(k) !== before) fillChunks();   // 游標進入倒數第二塊時接下一塊
      }
    } else S.beatNow = seg.startBeat;
    if (isScroll()) followFrame();
    updateNowChip();
    S.raf = requestAnimationFrame(animate);
  }
  function stop(rewind) {
    if (S.playing) {
      S.playing = false; clearInterval(S.schedTimer); cancelAnimationFrame(S.raf);
      if (player) player.cancelQueue(ctx);
      stopSynth();
      $("btnPlay").textContent = "▶ 播放";
      showHead();
    }
    if (rewind) { setCursor(loopRange()[0]); resumeFollow(); }
  }
  const togglePlay = () => (S.playing ? stop(false) : play());
  const restartIfPlaying = () => { if (S.playing) { stop(false); play(); } };

  // ---------- 區段 / 拍點區段 ----------
  function setLoop(start, end) {
    if (start === null) { S.loop = null; S.loopOn = false; }
    else { if (end < start) [start, end] = [end, start]; S.loop = { start, end }; S.loopOn = true; }
    updateLoopInputs(); drawOverlay(); scheduleSave(); restartIfPlaying();
  }
  function updateLoopInputs() {
    $("loopOn").checked = S.loopOn;
    if (S.loop) { $("loopStart").value = S.loop.start + 1; $("loopEnd").value = S.loop.end + 1; }
  }
  $("loopOn").addEventListener("change", (e) => {
    S.loopOn = e.target.checked;
    if (S.loopOn && !S.loop) S.loop = { start: +$("loopStart").value - 1 || 0, end: +$("loopEnd").value - 1 || 0 };
    drawOverlay(); scheduleSave(); restartIfPlaying();
  });
  for (const id of ["loopStart", "loopEnd"]) $(id).addEventListener("change", () => {
    const a = Math.max(1, +$("loopStart").value) - 1, b = Math.max(1, +$("loopEnd").value) - 1;
    setLoop(Math.min(a, nMeasures() - 1), Math.min(b, nMeasures() - 1));
  });
  $("setStart").addEventListener("click", () => { const m = measureOfStep(S.pos); setLoop(m, S.loop ? Math.max(S.loop.end, m) : m); });
  $("setEnd").addEventListener("click", () => { const m = measureOfStep(S.pos); setLoop(S.loop ? Math.min(S.loop.start, m) : 0, m); });
  $("clearLoop").addEventListener("click", () => setLoop(null));

  function setClickMode(mode) {
    S.clickMode = mode; $("clickMode").value = mode;
    if (S.osmd) buildTicks();
    renderClickRanges(); drawOverlay(); scheduleSave(); restartIfPlaying();
  }
  function renderClickRanges() {
    const el = $("clickRanges"); el.innerHTML = "";
    el.classList.toggle("hidden", S.clickMode !== "ranges");
    $("addClickRange").classList.toggle("hidden", S.clickMode !== "ranges");
    S.clickRanges.forEach(([a, b], i) => {
      const c = document.createElement("span"); c.className = "chip"; c.textContent = a === b ? `${a + 1}` : `${a + 1}–${b + 1}`;
      const x = document.createElement("b"); x.textContent = "×"; x.title = "移除";
      x.addEventListener("click", () => { S.clickRanges.splice(i, 1); setClickMode("ranges"); });
      c.appendChild(x); el.appendChild(c);
    });
  }
  $("clickMode").addEventListener("change", (e) => setClickMode(e.target.value));
  $("addClickRange").addEventListener("click", () => {
    const r = S.loop ? [S.loop.start, S.loop.end] : [measureOfStep(S.pos), measureOfStep(S.pos)];
    if (!S.clickRanges.some(([a, b]) => a === r[0] && b === r[1])) S.clickRanges.push(r);
    S.clickRanges.sort((p, q) => p[0] - q[0]);
    setClickMode("ranges");
  });

  // ---------- 覆蓋層 ----------
  const px = () => UNIT * zoomNow();
  function staffBox(m, staffIdx) {   // #score 座標（捲動模式加上該塊的位移）
    const v = viewOf(m); if (!v) return null; // 不在畫面上的頁／塊裡
    const gm = v.osmd.GraphicSheet?.MeasureList[m - v.from]?.[staffIdx];
    if (!gm) return null;
    const ap = gm.PositionAndShape.AbsolutePosition, sz = gm.PositionAndShape.Size, sl = gm.ParentStaffLine;
    if (!sz.width) return null;
    const y = sl ? sl.PositionAndShape.AbsolutePosition.y : ap.y, h = sl ? (sl.StaffHeight || 4) : 4;
    const off = viewOff(v), oy = horiz() ? v.div.offsetTop : 0;
    return { x: ap.x * px() + (horiz() ? off : 0), y: (y - 1.5) * px() + (horiz() ? oy : off), w: sz.width * px(), h: (h + 3) * px() };
  }
  function measureBox(m) { // 整個系統高度（所有譜表）
    const v = viewOf(m); if (!v) return null;
    const list = v.osmd.GraphicSheet?.MeasureList[m - v.from]; if (!list) return null;
    let box = null;
    list.forEach((gm, i) => {
      if (!gm) return; const b = staffBox(m, i); if (!b) return;
      if (!box) box = { ...b }; else { const bottom = Math.max(box.y + box.h, b.y + b.h); box.y = Math.min(box.y, b.y); box.h = bottom - box.y; box.w = Math.max(box.w, b.w); }
    });
    return box;
  }
  function addBox(cls, b, label) {
    if (!b) return null;
    const d = document.createElement("div"); d.className = cls;
    Object.assign(d.style, { left: b.x + "px", top: b.y + "px", width: b.w + "px", height: b.h + "px" });
    if (label) d.dataset.label = label;
    $("overlay").appendChild(d); return d;
  }
  function drawOverlay() {
    const ov = $("overlay"); ov.innerHTML = "";
    if (S.win.from < 0 || !S.osmd || !S.osmd.GraphicSheet) return;
    topCache = new Map();
    try { drawOverlayBoxes(); } finally { topCache = null; }
  }
  function drawOverlayBoxes() {
    const lo = S.win.from, hi = S.win.to;
    if (isScroll() && !horiz() && S.sections.length > 1) for (const v of S.chunks) {   // 樂章開頭：塊上方的間隔寫樂章名稱
      const sc = v.from > 0 && S.sections.find((s) => s.start === v.from);
      if (sc) addBox("sectitle", { x: 0, y: viewOff(v) - 26, w: 600, h: 20 }).textContent = sc.title;
    }
    if (S.loop && S.loopOn) for (let m = Math.max(lo, S.loop.start); m <= Math.min(S.loop.end, hi); m++) addBox("loop", measureBox(m));
    if (S.clickMode === "ranges") for (const [a, b] of S.clickRanges) for (let m = Math.max(lo, a); m <= Math.min(b, hi); m++) { const bx = measureBox(m); if (bx) addBox("click", { ...bx, h: 0 }); }
    const staffMine = S.parts[S.practice]?.staves[0];
    if (staffMine !== undefined) for (let m = lo; m <= hi; m++) addBox("mine", staffBox(m, staffMine));
    if (S.skipSolo) for (const k of S.skips) for (let m = Math.max(lo, k.start); m <= Math.min(k.end, hi); m++) addBox("skip", measureBox(m), m === k.start ? "跳過" : "");
    const mineOnly = $("errMineOnly").checked;
    for (const e of S.errors) {
      if (e.measure < lo || e.measure > hi) continue;
      if (mineOnly && e.part !== S.practice) continue;
      const staff = S.parts[e.part]?.staves[0] ?? e.part;
      addBox("err" + (e.empty ? " empty" : ""), staffBox(e.measure, staff), e.empty ? "空" : fmtBeats(e));
    }
    const sv = S.selected && S.selected.box && viewOf(S.selected.measure);
    if (sv) {   // box 是相對於該頁／塊的座標
      const r = 1.6 * px();
      const so = viewOff(sv), soy = horiz() ? sv.div.offsetTop : 0;
      addBox("sel", { x: S.selected.box.x + (horiz() ? so : 0) - r / 2, y: S.selected.box.y + (horiz() ? soy : so) - r / 2, w: r, h: r });
    }
  }
  function hitMeasure(x, y) {
    const v = viewAtPoint(x, y); if (!v) return -1;
    for (let m = v.from; m <= v.to; m++) { const b = measureBox(m); if (b && x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) return m; }
    return -1;
  }
  const scoreWrap = $("scoreWrap");
  function scorePoint(e) { const r = $("score").getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  scoreWrap.addEventListener("click", (e) => {
    if (!S.osmd || !S.steps.length || (isScroll() && showing)) return;   // 捲動模式正在排版要跳過去的位置：畫面上還是舊的樂譜，不接受點擊
    const { x, y } = scorePoint(e);
    if (S.edit) { selectNoteAt(x, y); return; }
    const m = hitMeasure(x, y); if (m < 0) return;
    if (e.shiftKey) setLoop(m, S.loop ? Math.max(S.loop.end, m) : m);
    else if (e.altKey) setLoop(S.loop ? Math.min(S.loop.start, m) : 0, m);
    else gotoMeasure(m);
  });
  let hoverEl = null;
  scoreWrap.addEventListener("mousemove", (e) => {
    if (!S.osmd || !S.osmd.GraphicSheet) return;
    const { x, y } = scorePoint(e); const m = hitMeasure(x, y);
    if (hoverEl) { hoverEl.remove(); hoverEl = null; }
    if (m >= 0 && !S.edit) hoverEl = addBox("hover", measureBox(m));
  });

  // ---------- 音符修正 ----------
  function selectNoteAt(x, y) {
    const v = viewAtPoint(x, y), voff = v ? viewOff(v) : 0, voy = v && horiz() ? v.div.offsetTop : 0;
    const gn = v && v.osmd.GraphicSheet.GetNearestNote(new OSMD.PointF2D((x - (horiz() ? voff : 0)) / px(), (y - (horiz() ? voy : voff)) / px()), 5);
    const measure = gn ? v.from + v.osmd.Sheet.SourceMeasures.indexOf(gn.sourceNote.SourceMeasure) : -1;
    if (!gn || measure > v.to) { S.selected = null; $("editStatus").textContent = ""; drawOverlay(); return; }   // 捲動模式：藏起來的最後一行不算
    const n = gn.sourceNote;
    const part = Math.max(0, v.osmd.Sheet.Instruments.indexOf(n.ParentVoiceEntry.ParentVoice.Parent));
    const ap = gn.PositionAndShape.AbsolutePosition;
    S.selected = { part, measure, onsetWhole: n.ParentVoiceEntry.Timestamp.RealValue, isRest: n.isRest(), midi: n.isRest() ? null : n.halfTone + 12, box: { x: ap.x * px(), y: ap.y * px() } };
    showSelectionInfo(); drawOverlay();
  }
  function showSelectionInfo() {
    const s = S.selected; if (!s) return;
    const err = S.errors.find((e) => e.part === s.part && e.measure === s.measure);
    const mEl = xmlMeasures(s.part)[s.measure];
    let beatsTxt = "";
    if (mEl) { const div = divisionsAt(xmlMeasures(s.part), s.measure); const { max } = measureFill(mEl); const exp = err ? err.expected : null; beatsTxt = `　此小節時值 ${+(max / div).toFixed(2)} 拍` + (err ? `（應為 ${+(exp / div).toFixed(2)}）` : ""); }
    $("editStatus").textContent = `　已選：${S.parts[s.part]?.name || ""} 第 ${s.measure + 1} 小節 ${s.isRest ? "休止符" : midiName(s.midi)}${beatsTxt}`;
  }
  const NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  const midiName = (m) => NAMES[m % 12] + (Math.floor(m / 12) - 1);
  function divisionsAt(measures, idx) {
    let d = 1;
    for (let i = 0; i <= idx; i++) measures[i].querySelectorAll(":scope > attributes > divisions").forEach((el) => { d = +el.textContent || d; });
    return d;
  }
  function xmlNotesOf(mEl) {
    let pos = 0, last = 0; const out = [];
    for (const el of mEl.children) {
      if (el.tagName === "backup") pos -= durOf(el);
      else if (el.tagName === "forward") pos += durOf(el);
      else if (el.tagName === "note") {
        const chord = !!el.querySelector(":scope > chord"), grace = !!el.querySelector(":scope > grace");
        out.push({ el, onset: chord ? last : pos });
        if (!chord) { last = pos; if (!grace) pos += durOf(el); }
      }
    }
    return out;
  }
  function xmlMidi(el) {
    const p = el.querySelector(":scope > pitch"); if (!p) return null;
    const step = p.querySelector("step").textContent, oct = +p.querySelector("octave").textContent, alt = +(p.querySelector("alter")?.textContent || 0);
    return (oct + 1) * 12 + { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[step] + alt;
  }
  function findXmlNote(sel) {
    const measures = xmlMeasures(sel.part); const mEl = measures[sel.measure]; if (!mEl) return null;
    const div = divisionsAt(measures, sel.measure), target = Math.round(sel.onsetWhole * 4 * div);
    const cands = xmlNotesOf(mEl).filter((x) => Math.abs(x.onset - target) <= 0.5);
    const hit = cands.find((x) => (sel.isRest ? !!x.el.querySelector(":scope > rest") : xmlMidi(x.el) === sel.midi)) || cands[0];
    return hit ? { el: hit.el, div, measures } : null;
  }
  function spell(midi, preferFlat) {
    const sharp = [["C", 0], ["C", 1], ["D", 0], ["D", 1], ["E", 0], ["F", 0], ["F", 1], ["G", 0], ["G", 1], ["A", 0], ["A", 1], ["B", 0]];
    const flat = [["C", 0], ["D", -1], ["D", 0], ["E", -1], ["E", 0], ["F", 0], ["G", -1], ["G", 0], ["A", -1], ["A", 0], ["B", -1], ["B", 0]];
    const [step, alter] = (preferFlat ? flat : sharp)[((midi % 12) + 12) % 12];
    return { step, alter, octave: Math.floor(midi / 12) - 1 };
  }
  function keyPrefersFlat(measures, idx) {
    let f = 0;
    for (let i = 0; i <= idx; i++) measures[i].querySelectorAll(":scope > attributes > key > fifths").forEach((el) => { f = +el.textContent; });
    return f < 0;
  }
  function insertBeforeDuration(el, node) { el.insertBefore(node, el.querySelector(":scope > duration") || el.querySelector(":scope > type") || null); }
  function setPitch(el, midi, preferFlat) {
    for (const t of ["pitch", "rest", "accidental", "unpitched"]) el.querySelector(`:scope > ${t}`)?.remove();
    const { step, alter, octave } = spell(midi, preferFlat);
    const p = S.xmlDoc.createElement("pitch");
    const mk = (tag, v) => { const x = S.xmlDoc.createElement(tag); x.textContent = String(v); p.appendChild(x); };
    mk("step", step); if (alter) mk("alter", alter); mk("octave", octave);
    insertBeforeDuration(el, p);
  }
  function toRest(el) {
    for (const t of ["pitch", "accidental", "stem", "notations", "beam", "tie", "unpitched"]) el.querySelectorAll(`:scope > ${t}`).forEach((x) => x.remove());
    if (!el.querySelector(":scope > rest")) insertBeforeDuration(el, S.xmlDoc.createElement("rest"));
  }
  const TYPES = { w: ["whole", 1], h: ["half", 1 / 2], q: ["quarter", 1 / 4], e: ["eighth", 1 / 8], s: ["16th", 1 / 16], t: ["32nd", 1 / 32] };
  function setDuration(el, div, typeKey, toggleDot) {
    const members = [el]; let nx = el.nextElementSibling;
    while (nx && nx.tagName === "note" && nx.querySelector(":scope > chord")) { members.push(nx); nx = nx.nextElementSibling; }
    for (const n of members) {
      let typeEl = n.querySelector(":scope > type");
      if (typeKey) { if (!typeEl) { typeEl = S.xmlDoc.createElement("type"); n.appendChild(typeEl); } typeEl.textContent = TYPES[typeKey][0]; }
      const typeName = typeEl ? typeEl.textContent : "quarter";
      const frac = (Object.values(TYPES).find((t) => t[0] === typeName) || TYPES.q)[1];
      let dots = n.querySelectorAll(":scope > dot").length;
      if (toggleDot) { dots = dots ? 0 : 1; n.querySelectorAll(":scope > dot").forEach((x) => x.remove()); if (dots) { const d = S.xmlDoc.createElement("dot"); typeEl.nextSibling ? n.insertBefore(d, typeEl.nextSibling) : n.appendChild(d); } }
      let mult = dots ? 1.5 : 1;
      const tm = n.querySelector(":scope > time-modification");
      if (tm) mult *= +tm.querySelector("normal-notes").textContent / +tm.querySelector("actual-notes").textContent;
      const durEl = n.querySelector(":scope > duration");
      if (durEl && !n.querySelector(":scope > grace")) durEl.textContent = String(Math.max(1, Math.round(frac * 4 * div * mult)));
    }
  }
  function setTimeSignature(partIdx, measureIdx, text) {
    const m = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(text || ""); if (!m) return false;
    const mEl = xmlMeasures(partIdx)[measureIdx]; if (!mEl) return false;
    let attrs = mEl.querySelector(":scope > attributes");
    if (!attrs) { attrs = S.xmlDoc.createElement("attributes"); mEl.insertBefore(attrs, mEl.firstElementChild); }
    mEl.querySelectorAll(":scope > attributes > time").forEach((t) => t.remove());
    const t = S.xmlDoc.createElement("time");
    const b = S.xmlDoc.createElement("beats"); b.textContent = m[1]; const bt = S.xmlDoc.createElement("beat-type"); bt.textContent = m[2];
    t.append(b, bt);
    const after = attrs.querySelector(":scope > key"); const before = attrs.querySelector(":scope > staves, :scope > clef");
    if (after && after.nextSibling) attrs.insertBefore(t, after.nextSibling); else if (before) attrs.insertBefore(t, before); else attrs.appendChild(t);
    return true;
  }
  function nominalDivisions(partIdx, measureIdx) {
    const measures = xmlMeasures(partIdx);
    let div = 1, beats = 4, beatType = 4;
    for (let i = 0; i <= measureIdx; i++) for (const a of measures[i].querySelectorAll(":scope > attributes")) {
      const d = a.querySelector("divisions"); if (d) div = +d.textContent || div;
      const t = a.querySelector("time"); if (t) { beats = +t.querySelector("beats")?.textContent || beats; beatType = +t.querySelector("beat-type")?.textContent || beatType; }
    }
    return Math.round(div * 4 * beats / beatType);
  }
  async function insertMeasures() { // 在選取的小節之後插入 N 個整小節休止（用來補多小節休止／對齊各聲部）
    const sel = S.selected; if (!sel) return;
    const n = parseInt(prompt(`${S.parts[sel.part]?.name} 第 ${sel.measure + 1} 小節之後插入幾個空小節？（多小節休止請填「小節數 − 1」）`, "1"), 10);
    if (!n || n < 1 || n > 200) return;
    S.undo.push(S.xmlText); if (S.undo.length > 50) S.undo.shift();
    const mEl = xmlMeasures(sel.part)[sel.measure], nominal = nominalDivisions(sel.part, sel.measure);
    let anchor = mEl;
    for (let i = 0; i < n; i++) {
      const m = S.xmlDoc.createElement("measure"); m.setAttribute("number", "0");
      const note = S.xmlDoc.createElement("note"); const r = S.xmlDoc.createElement("rest"); r.setAttribute("measure", "yes");
      const d = S.xmlDoc.createElement("duration"); d.textContent = String(nominal);
      note.append(r, d); m.appendChild(note);
      anchor.parentNode.insertBefore(m, anchor.nextSibling); anchor = m;
    }
    await reloadFromDoc();
  }
  async function deleteMeasure() { // 刪除選取的小節（OMR 多認出來的小節）；attributes 移到下一小節
    const sel = S.selected; if (!sel) return;
    if (!confirm(`刪除 ${S.parts[sel.part]?.name} 第 ${sel.measure + 1} 小節？`)) return;
    S.undo.push(S.xmlText); if (S.undo.length > 50) S.undo.shift();
    const measures = xmlMeasures(sel.part), mEl = measures[sel.measure], next = measures[sel.measure + 1];
    if (next) for (const a of Array.from(mEl.querySelectorAll(":scope > attributes")).reverse()) next.insertBefore(a, next.firstElementChild);
    mEl.remove();
    S.selected = null;
    await reloadFromDoc();
  }
  async function editTimeSignature() {
    const sel = S.selected; if (!sel) return;
    const text = prompt(`${S.parts[sel.part]?.name} 第 ${sel.measure + 1} 小節起的拍號（例如 6/4）：`);
    if (!text) return;
    S.undo.push(S.xmlText); if (S.undo.length > 50) S.undo.shift();
    if (!setTimeSignature(sel.part, sel.measure, text)) { alert("格式錯誤，請輸入例如 6/4"); S.undo.pop(); return; }
    await reloadFromDoc();
  }
  function removeNote(el) {
    const nx = el.nextElementSibling;
    if (!el.querySelector(":scope > chord") && nx && nx.tagName === "note") nx.querySelector(":scope > chord")?.remove();
    el.remove();
  }
  async function applyEdit(fn) {
    if (!S.selected) return;
    const found = findXmlNote(S.selected);
    if (!found) { $("editStatus").textContent = "　找不到對應的 XML 音符"; return; }
    S.undo.push(S.xmlText); if (S.undo.length > 50) S.undo.shift();
    const sel = { ...S.selected };
    const result = fn(found.el, found.div, found.measures);
    if (result && result.midi !== undefined) sel.midi = result.midi;
    if (result && result.isRest !== undefined) sel.isRest = result.isRest;
    S.selected = result && result.deleted ? null : sel;
    await reloadFromDoc();
  }
  async function reloadFromDoc() {
    const xml = new XMLSerializer().serializeToString(S.xmlDoc);
    const sel = S.selected;
    S.pendingMeasure = sel ? sel.measure : measureOfStep(S.pos);
    await showScore(xml, true, "");
    if (sel) {
      S.selected = sel;
      const gn = findGraphicalNote(sel);
      if (gn) { const ap = gn.PositionAndShape.AbsolutePosition; sel.box = { x: ap.x * px(), y: ap.y * px() }; }
      const k = S.measureFirstStep[sel.measure]; if (k !== undefined) setCursor(k);
      showSelectionInfo(); drawOverlay();
    }
    scheduleSave(true);
  }
  function findGraphicalNote(sel) {
    const v = viewOf(sel.measure); if (!v) return null;
    const list = v.osmd.GraphicSheet.MeasureList[sel.measure - v.from]; if (!list) return null;
    for (const gm of list) {
      if (!gm) continue;
      for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) {
        const n = gn.sourceNote;
        if (v.osmd.Sheet.Instruments.indexOf(n.ParentVoiceEntry.ParentVoice.Parent) !== sel.part) continue;
        if (Math.abs(n.ParentVoiceEntry.Timestamp.RealValue - sel.onsetWhole) > 1e-6) continue;
        if (sel.isRest ? n.isRest() : (!n.isRest() && n.halfTone + 12 === sel.midi)) return gn;
      }
    }
    return null;
  }
  async function undo() {
    const prev = S.undo.pop(); if (!prev) return;
    S.selected = null; S.pendingMeasure = measureOfStep(S.pos); await showScore(prev, true, ""); scheduleSave(true);
  }

  // ---------- 設定存取 ----------
  // 設定存在瀏覽器（每個瀏覽器各記各的）：sp.settings.<作品>。伺服器上的舊設定只在第一次開這個作品時讀進來當預設。
  const settingsKey = (slug) => `sp.settings.${slug}`;
  async function loadSettings(slug) {
    try { const st = JSON.parse(localStorage.getItem(settingsKey(slug))); if (st) return st; } catch (e) { /* 無痕／被擋：退回伺服器 */ }
    try { const st = await api(`/api/works/${slug}/settings`); return st && Object.keys(st).length ? st : null; } catch (e) { return null; }
  }
  function collectSettings() {
    return { speed: S.speed, speeds: S.speeds, loop: S.loop, loopOn: S.loopOn, posMeasure: measureOfStep(S.pos), measures: nMeasures(), clickMode: S.clickMode, clickRanges: S.clickRanges,
      countIn: S.countIn, zoom: S.zoom, volume: S.volume, myVolume: S.myVolume, tuning: S.tuning, soundMode: S.soundMode, panning: S.panning, skipSolo: S.skipSolo, skipMin: S.skipMin, skipKeep: S.skipKeep, practice: S.parts[S.practice]?.xmlId, mutes: S.parts.filter((p) => p.mute).map((p) => p.xmlId) };
  }
  function saveNow() {
    clearTimeout(S.saveTimer); S.saveTimer = null;
    if (!S.slug || S.settingsFor !== S.slug) return;
    const st = collectSettings();
    try { localStorage.setItem(settingsKey(S.slug), JSON.stringify(st)); S.savedMeasure = st.posMeasure; } catch (e) { /* 空間滿或被擋：這次不記 */ }
    if (S.saveScore) {
      S.saveScore = false;
      fetch(`/api/works/${S.slug}/score.musicxml`, { method: "PUT", headers: { "content-type": "application/xml" }, body: S.xmlText })
        .then((r) => { $("editStatus").textContent += r.ok ? "　已儲存" : "　儲存失敗"; }, () => { $("editStatus").textContent += "　儲存失敗"; });
    }
  }
  function scheduleSave(scoreChanged) {
    if (scoreChanged) S.saveScore = true;
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(saveNow, 800);
  }
  const flushSave = () => { if (S.saveTimer) saveNow(); };
  window.addEventListener("pagehide", flushSave);                                        // 關分頁／重新整理前把最後的位置寫進去
  document.addEventListener("visibilitychange", () => { if (document.hidden) flushSave(); });
  function applySettings(st) {
    S.speeds = [];
    if (Array.isArray(st.speeds)) S.speeds = st.speeds.map((v) => (v ? Math.max(0.2, Math.min(1.5, v)) : undefined));
    if (st.speed) S.speed = Math.max(0.2, Math.min(1.5, st.speed));   // 舊設定／沒有個別設定的樂章用這個
    rebuildTimeMap();
    // 位置、區段是以小節為單位的；如果存的是另一份譜（小節數不同，例如同名的舊 OMR 版），這些就不沿用
    const sameScore = st.measures === nMeasures();
    if (!sameScore) { st = { ...st, loop: null, loopOn: false, clickRanges: [], posMeasure: 0 }; }
    S.loop = st.loop || null; S.loopOn = !!st.loopOn && !!S.loop;
    if (st.clickMode) S.clickMode = st.clickMode; else if (st.metronome) S.clickMode = "all";
    S.clickRanges = Array.isArray(st.clickRanges) ? st.clickRanges : [];
    if (st.countIn !== undefined) { S.countIn = !!st.countIn; $("countIn").checked = S.countIn; }
    if (st.volume !== undefined) { S.volume = st.volume; $("volume").value = st.volume; }
    if (st.tuning) { S.tuning = st.tuning; $("tuning").value = st.tuning; }
    if (st.soundMode) { S.soundMode = st.soundMode; $("soundMode").value = st.soundMode; }
    if (st.skipSolo !== undefined) { S.skipSolo = !!st.skipSolo; $("skipSolo").checked = S.skipSolo; }
    if (st.skipMin) { S.skipMin = st.skipMin; $("skipMin").value = st.skipMin; }
    if (st.skipKeep !== undefined) { S.skipKeep = st.skipKeep; $("skipKeep").value = st.skipKeep; }
    computeSkips();
    if (st.panning !== undefined) { S.panning = !!st.panning; $("panning").checked = S.panning; }
    if (st.myVolume !== undefined) S.myVolume = st.myVolume;
    const pr = S.parts.findIndex((p) => p.xmlId === st.practice); if (pr >= 0) S.practice = pr;
    for (const p of S.parts) p.mute = (st.mutes || []).includes(p.xmlId);
    if (st.zoom && st.zoom !== S.zoom) {
      S.zoom = st.zoom; $("zoomVal").textContent = Math.round(S.zoom * 100) + "%";
      if (S.win.from >= 0) redisplay();   // 還沒畫第一頁時不必重畫
    }
    $("clickMode").value = S.clickMode; buildTicks(); renderClickRanges(); renderPartsUI();
    updateLoopInputs();
    drawOverlay();
  }


  // ---------- 工具列：練習／設定面板、自動隱藏、播放中的位置指示 ----------
  // 規則：載入後顯示幾秒 → 收起；滑鼠移到畫面上緣（或按上緣小標籤）叫出來；滑鼠在工具列上、
  // 或游標在工具列的文字欄／選單裡時一直留著；離開幾秒後收起；按播放立刻收起。工具列是蓋在樂譜上的，
  // 收起／展開不會動到捲動位置。按鈕、勾選框、滑桿有焦點不算：Chrome 點按鈕就會讓它取得焦點，
  // 算進去的話用滑鼠按 ▶ 之後工具列就一直不收。
  const HEAD_SHOW_MS = 4000, HEAD_HIDE_MS = 1500;
  const headEl = $("headerStack"), peekEl = $("peekTab"), chipEl = $("nowChip");
  let headTimer = null, headOver = false;
  const typingInHead = () => headEl.contains(document.activeElement) && document.activeElement.matches("input[type=text], input[type=number], select");
  const headPinned = () => headOver || typingInHead() || !!headEl.querySelector(".pop.open");
  const autoHideOn = () => { try { return localStorage.getItem("toolbarAutoHide") !== "off"; } catch (e) { return true; } };
  function hideHead() {
    if (!autoHideOn() || headPinned() || (S.edit && !S.playing)) return;   // 關掉自動隱藏、或修正模式（沒在播放）時留著
    headEl.classList.add("away");
    updateNowChip();
  }
  function showHead(holdMs) {
    clearTimeout(headTimer);
    headEl.classList.remove("away");
    updateNowChip();
    if (holdMs !== 0) headTimer = setTimeout(hideHead, holdMs || HEAD_HIDE_MS);
  }
  document.addEventListener("mousemove", (e) => { if (e.clientY <= 12 && headEl.classList.contains("away")) showHead(); }, { passive: true });
  headEl.addEventListener("mouseenter", () => { headOver = true; clearTimeout(headTimer); headEl.classList.remove("away"); updateNowChip(); });
  headEl.addEventListener("mouseleave", () => {
    headOver = false;
    // 已經收起時不理：觸控螢幕要到下一次點別處才補送「離開」，點上緣標籤時會先收到它，
    // 照常處理的話工具列先跳出來、標籤變成點不到，這一下就點穿到樂譜上
    if (headEl.classList.contains("away")) return;
    if (!S.playing) showHead(); else hideHead();
  });
  headEl.addEventListener("focusin", () => { clearTimeout(headTimer); headEl.classList.remove("away"); updateNowChip(); });
  headEl.addEventListener("focusout", () => { if (!headPinned() && !headEl.classList.contains("away")) showHead(); });   // 同上：收起時焦點離開不必叫出來
  // 點上緣標籤或右上角的位置：觸控沒有「滑鼠停在工具列上」可以留住它，給手指時間去點
  for (const el of [peekEl, chipEl]) el.addEventListener("click", () => showHead(HEAD_SHOW_MS));
  document.addEventListener("click", (e) => {   // 練習／設定面板
    const toggle = e.target.closest("[data-poptoggle]");
    for (const p of document.querySelectorAll(".pop.open")) if (!toggle || p !== toggle.closest(".pop")) p.classList.remove("open");
    if (toggle) { toggle.closest(".pop").classList.toggle("open"); clearTimeout(headTimer); }
  });
  function updateNowChip() {   // 工具列收起時顯示：樂章、小節、第幾拍
    const away = headEl.classList.contains("away");
    chipEl.classList.toggle("hidden", !away || !S.ready);
    if (!away || !S.ready) return;
    const m = measureOfStep(S.pos), [num, den] = S.timeSig[m] || [4, 4], beatLen = 4 / den;
    const base = S.measureStartBeat[m] ?? 0;
    const now = S.playing ? S.beatNow : S.steps[S.pos]?.beat ?? base;
    const beat = Math.min(num, Math.max(1, Math.floor((now - base) / beatLen) + 1));
    const where = S.sections.length > 1 ? `${S.sections[sectionOf(m)].title.split("　")[0]} ` : "";
    chipEl.textContent = `${where}第 ${localMeasure(m)} 小節 · 第 ${beat} 拍`;
  }

  // ---------- 控制項 ----------
  function setSpeed(v, save = true) {
    const sp = Math.max(0.2, Math.min(1.5, Math.round(v * 100) / 100));
    S.speeds[sectionOf(measureOfStep(S.pos))] = sp;   // 只改目前所在樂章
    S.speed = sp;
    rebuildTimeMap(); updateSpeedLabel();
    if (save) { scheduleSave(); restartIfPlaying(); }
  }
  $("bpm").addEventListener("input", (e) => setSpeed(+e.target.value / 100));
  document.querySelectorAll(".chip[data-speed]").forEach((c) => c.addEventListener("click", () => setSpeed(+c.dataset.speed)));
  $("btnPlay").addEventListener("click", togglePlay);
  $("btnStop").addEventListener("click", () => stop(false));  // 停在目前位置；回到起點用 Home 鍵
  $("countIn").addEventListener("change", (e) => { S.countIn = e.target.checked; scheduleSave(); });
  $("volume").addEventListener("input", (e) => { S.volume = +e.target.value; scheduleSave(); });
  function skipChanged() { S.skipSolo = $("skipSolo").checked; S.skipMin = Math.max(1, +$("skipMin").value || 8); S.skipKeep = Math.max(0, +$("skipKeep").value || 0); computeSkips(); drawOverlay(); scheduleSave(); restartIfPlaying(); }
  for (const id of ["skipSolo", "skipMin", "skipKeep"]) $(id).addEventListener("change", skipChanged);
  $("soundMode").addEventListener("change", (e) => { S.soundMode = e.target.value; scheduleSave(); restartIfPlaying(); });
  $("panning").addEventListener("change", (e) => { S.panning = e.target.checked; scheduleSave(); });
  $("tuning").addEventListener("change", (e) => { S.tuning = Math.max(400, Math.min(480, +e.target.value || 442)); e.target.value = S.tuning; scheduleSave(); restartIfPlaying(); });
  function setZoom(z) {
    if (horiz()) { S.zoomH = Math.max(0.4, Math.min(2.5, z)); $("zoomVal").textContent = Math.round(S.zoomH * 100) + "%"; }
    else { S.zoom = Math.max(0.4, Math.min(2.5, z)); $("zoomVal").textContent = Math.round(S.zoom * 100) + "%"; }
    if (!S.osmd) return;
    redisplay().then(() => setCursor(S.pos)); scheduleSave();
  }
  $("zoomIn").addEventListener("click", () => setZoom(zoomNow() + 0.1));
  $("zoomOut").addEventListener("click", () => setZoom(zoomNow() - 0.1));
  $("editMode").addEventListener("change", (e) => {
    S.edit = e.target.checked; scoreWrap.classList.toggle("edit", S.edit); $("editHint").classList.toggle("hidden", !S.edit);
    document.body.classList.toggle("editing", S.edit);   // 修正模式：工具列改成推開樂譜
    if (S.edit) showHead(0); else showHead();
    if (!S.edit) { S.selected = null; drawOverlay(); }
  });
  $("showPages").addEventListener("change", (e) => $("pages").classList.toggle("hidden", !e.target.checked));
  $("btnHelp").addEventListener("click", () => $("help").showModal());

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea") || document.querySelector("dialog[open]")) { if (e.key === "Escape") e.target.blur?.(); return; }
    const k = e.key;
    if (k === " ") { e.preventDefault(); togglePlay(); return; }
    if (k === "Escape") { if (S.selected) { S.selected = null; $("editStatus").textContent = ""; drawOverlay(); } else if (S.edit) { $("editMode").checked = false; $("editMode").dispatchEvent(new Event("change")); } return; }
    if ((e.metaKey || e.ctrlKey) && k.toLowerCase() === "z") { e.preventDefault(); undo(); return; }
    if (S.edit && S.selected) {
      const flat = keyPrefersFlat(xmlMeasures(S.selected.part), S.selected.measure);
      if (k === "ArrowUp" || k === "ArrowDown") {
        e.preventDefault(); if (S.selected.isRest) return;
        const midi = S.selected.midi + (k === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 12 : 1);
        const part = S.selected.part;
        applyEdit((el) => { setPitch(el, midi, flat); return { midi }; });
        ensureAudio(); playNote({ midi, part }, ctx.currentTime, 0.5);
        return;
      }
      if (k === "Delete" || k === "Backspace") {
        e.preventDefault();
        if (e.shiftKey) applyEdit((el) => { removeNote(el); return { deleted: true }; });
        else applyEdit((el) => { toRest(el); return { isRest: true, midi: null }; });
        return;
      }
      if (k === "T" && e.shiftKey) { e.preventDefault(); editTimeSignature(); return; }
      if (k.toLowerCase() === "i") { e.preventDefault(); insertMeasures(); return; }
      if (k.toLowerCase() === "x") { e.preventDefault(); deleteMeasure(); return; }
      if (k.toLowerCase() === "n" && S.selected.isRest) { applyEdit((el) => { setPitch(el, 71, flat); return { isRest: false, midi: 71 }; }); return; }
      if (TYPES[k.toLowerCase()] && !e.shiftKey) { applyEdit((el, div) => setDuration(el, div, k.toLowerCase(), false)); return; }
      if (k === ".") { applyEdit((el, div) => setDuration(el, div, null, true)); return; }
    }
    if (k === "Home") { e.preventDefault(); stop(true); return; }
    if (k === "PageUp") { e.preventDefault(); pageStep(-1); return; }
    if (k === "PageDown") { e.preventDefault(); pageStep(1); return; }
    if (k === "ArrowLeft") { e.preventDefault(); gotoMeasure(measureOfStep(S.pos) - 1); return; }
    if (k === "ArrowRight") { e.preventDefault(); gotoMeasure(measureOfStep(S.pos) + 1); return; }
    if (k === "[") { $("setStart").click(); return; }
    if (k === "]") { $("setEnd").click(); return; }
    if (k.toLowerCase() === "l") { $("loopOn").checked = !S.loopOn; $("loopOn").dispatchEvent(new Event("change")); return; }
    if (k.toLowerCase() === "m") { setClickMode(S.clickMode === "off" ? "all" : S.clickMode === "all" ? "ranges" : "off"); return; }
    if (k.toLowerCase() === "e") { gotoError(e.shiftKey ? -1 : 1); return; }
    if (k === "+" || k === "=") { setSpeed(speedAt(measureOfStep(S.pos)) + 0.05); return; }
    if (k === "-") { setSpeed(speedAt(measureOfStep(S.pos)) - 0.05); return; }
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
    const now = horiz() ? wrapEl.clientHeight : scoreEl.clientWidth;
    if (isScroll() && now === S.layoutWidth) { S.follow.dirty = true; return; }   // 只有另一個方向變了就不必重排
    if (S.osmd && S.steps.length) redisplay().then(() => setCursor(S.pos));
  }, 200); });
  document.addEventListener("pointerdown", () => ensureAudio(), { once: true });
  document.addEventListener("touchstart", () => {}, { passive: true });   // iOS Safari：有 touchstart 監聽，按鈕的 :active（按下去變深）才會出現
  window.SP = S;
  showHead(HEAD_SHOW_MS);
  refreshWorks();
})();
