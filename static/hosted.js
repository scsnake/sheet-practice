/* 靜態網站模式（hosted）：沒有伺服器。作品存在這個瀏覽器的 IndexedDB 裡，只收 export_ready.py 做的「樂譜包」ZIP。
   app.js 照常呼叫 /api/...，這裡回答同樣的資料；不做任何轉換（轉換和檢查在匯入前由 agent 做好）。
   只在 index.html 有 <meta name="sp-mode" content="hosted"> 時啟用（build_static.py 會設好）。 */
(() => {
  if (document.querySelector('meta[name="sp-mode"]')?.content !== "hosted") return;

  const FORMAT = "sheet-practice-ready", VERSION = 1, MANIFEST = "sheet-practice-ready.json";
  const MAX_ENTRY = 64 * 1024 * 1024;   // 解壓後一個檔案最大 64 MB（合併譜通常 1–10 MB）
  const LOCAL_ONLY = "這個功能只在本機版（./run.sh）有";

  // ---------- IndexedDB：works = 作品資訊（清單用，很小）；scores = 合併譜和時間軸（很大，打開作品時才讀） ----------
  let dbPromise = null;
  const db = () => dbPromise || (dbPromise = new Promise((ok, bad) => {
    const r = indexedDB.open("sheet-practice", 1);
    r.onupgradeneeded = () => {
      r.result.createObjectStore("works", { keyPath: "slug" });
      r.result.createObjectStore("scores", { keyPath: "slug" });
    };
    r.onsuccess = () => ok(r.result);
    r.onerror = () => bad(r.error);
    r.onblocked = () => bad(new Error("資料庫被另一個分頁占用：請關掉其他分頁再試"));
  }));
  async function run(stores, mode, fn) {   // fn(各 store) 只排請求；全部寫完（交易完成）才 resolve
    const t = (await db()).transaction(stores, mode);
    const result = {};
    fn(...stores.map((s) => t.objectStore(s)), result);
    return new Promise((ok, bad) => {
      t.oncomplete = () => ok(result);
      t.onerror = () => bad(t.error);
      t.onabort = () => bad(t.error || new Error("儲存被取消"));
    });
  }
  const get = (store, key) => run([store], "readonly", (s, out) => { s.get(key).onsuccess = (e) => { out.value = e.target.result; }; }).then((o) => o.value);
  const getAll = (store) => run([store], "readonly", (s, out) => { s.getAll().onsuccess = (e) => { out.value = e.target.result; }; }).then((o) => o.value || []);
  const putBoth = (work, score) => run(["works", "scores"], "readwrite", (w, s) => { w.put(work); s.put(score); });
  const put = (store, value) => run([store], "readwrite", (s) => { s.put(value); });
  const remove = (slug) => run(["works", "scores"], "readwrite", (w, s) => { w.delete(slug); s.delete(slug); });

  // ---------- ZIP：讀中央目錄，deflate 用瀏覽器內建的 DecompressionStream ----------
  async function unzip(buf) {
    const u8 = new Uint8Array(buf), dv = new DataView(buf);
    let end = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 22 - 65535); i--) if (dv.getUint32(i, true) === 0x06054b50) { end = i; break; }
    if (end < 0) throw new Error("不是 ZIP 檔");
    const count = dv.getUint16(end + 10, true);
    let p = dv.getUint32(end + 16, true);
    const entries = new Map(), utf8 = new TextDecoder();
    for (let k = 0; k < count; k++) {
      if (p + 46 > u8.length || dv.getUint32(p, true) !== 0x02014b50) throw new Error("ZIP 檔損壞");
      const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true);
      const csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
      const nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true), off = dv.getUint32(p + 42, true);
      const name = utf8.decode(u8.subarray(p + 46, p + 46 + nl));
      if (!name.endsWith("/")) entries.set(name, { flags, method, csize, usize, off });
      p += 46 + nl + xl + cl;
    }
    async function bytes(name) {
      const e = entries.get(name);
      if (e.flags & 1) throw new Error(`${name} 有加密，不能解壓`);
      if (e.usize > MAX_ENTRY) throw new Error(`${name} 太大（超過 ${MAX_ENTRY / 1048576} MB）`);
      const start = e.off + 30 + dv.getUint16(e.off + 26, true) + dv.getUint16(e.off + 28, true);
      const data = u8.subarray(start, start + e.csize);
      if (e.method === 0) return data;
      if (e.method !== 8) throw new Error(`${name} 用了不支援的壓縮法`);
      // 邊解壓邊數：usize 可以是假的，不可以先把全部解出來才檢查大小
      const reader = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader();
      const chunks = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.length;
        if (total > MAX_ENTRY) { reader.cancel().catch(() => {}); throw new Error(`${name} 太大（超過 ${MAX_ENTRY / 1048576} MB）`); }
        chunks.push(value);
      }
      const out = new Uint8Array(total);
      let at = 0;
      for (const c of chunks) { out.set(c, at); at += c.length; }
      return out;
    }
    return { names: [...entries.keys()], bytes, text: async (name) => utf8.decode(await bytes(name)) };
  }

  // ---------- 匯入一個樂譜包 ----------
  const newStamp = () => [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const SCORE_FILE = /\.(musicxml|xml|mxl)$/i;
  async function importPackage(fileName, buf, prog) {
    prog.set("read", 0, 1);
    let z;
    try { z = await unzip(buf); } catch (e) { throw new Error(`${fileName}：${e.message}`); }
    const visible = z.names.filter((n) => !n.split("/").some((x) => x.startsWith(".") || x === "__MACOSX"));
    const found = visible.filter((n) => n.split("/").pop() === MANIFEST).sort((a, b) => a.split("/").length - b.split("/").length)[0];
    if (!found) {
      if (visible.some((n) => SCORE_FILE.test(n) || /(^|\/)sheet-practice\.json$/.test(n)))
        throw new Error(`${fileName} 是原始樂譜，還沒有做成樂譜包。網頁版不轉換樂譜：請先用 check_import.py --export 做成樂譜包（見 docs/AGENT_SCORE_GUIDE.md），或用本機版（./run.sh）匯入。`);
      throw new Error(`${fileName} 不是 sheet-practice 的樂譜包（ZIP 裡沒有 ${MANIFEST}）`);
    }
    const base = found.slice(0, found.length - MANIFEST.length);
    let m;
    const manifestText = await z.text(found);   // 太大時是這個錯誤，不是「不是正確的 JSON」
    try { m = JSON.parse(manifestText); } catch (e) { throw new Error(`${MANIFEST} 不是正確的 JSON：${e.message}`); }
    if (m?.format !== FORMAT) throw new Error(`${MANIFEST} 的格式不對`);
    if (!(m.version <= VERSION)) throw new Error("這個樂譜包比這個網頁新：請重新整理頁面（或更新網站）再試");
    // slug 的規則和 works.slug() 一樣：Unicode 的字母和數字、「_」、「-」，最多 60 個字
    if (typeof m.slug !== "string" || !/^[\p{L}\p{N}_-]{1,60}$/u.test(m.slug) || typeof m.work !== "string" || !Array.isArray(m.parts) || !m.parts.length)
      throw new Error(`${MANIFEST} 缺少作品名稱或聲部`);
    const scoreName = base + (m.files?.score || "score.musicxml"), tlName = base + (m.files?.timeline || "timeline.json");
    if (!z.names.includes(scoreName)) throw new Error(`樂譜包裡沒有 ${scoreName}`);
    prog.set("build", 0, 1);
    const xml = await z.text(scoreName);
    if (!xml.slice(0, 4000).includes("<score-partwise")) throw new Error(`${scoreName} 不是 MusicXML（score-partwise）`);
    let timeline = null;
    if (z.names.includes(tlName)) { try { timeline = JSON.parse(await z.text(tlName)); } catch { timeline = null; } }   // 沒有時間軸也可以：瀏覽器自己分析
    const old = await get("works", m.slug);
    if (old?.edited && !confirm(`「${old.work}」已經在這個瀏覽器裡，而且你改過音符。\n\n換成這個樂譜包的版本嗎？改過的音符會不見。`))
      throw Object.assign(new Error(`沒有匯入：保留你改過的「${old.work}」`), { cancelled: true });
    prog.set("check", 0, 1);
    const s = m.summary || {};
    const stamp = newStamp();
    const work = {
      slug: m.slug, work: m.work, created: Date.now() / 1000, edited: false,
      parts: m.parts.map((p) => ({ part: String(p.part), xml_id: String(p.xml_id || ""), measures: p.measures ?? null, rights: p.rights || "", source: p.source || "" })),
      settings: m.settings && typeof m.settings === "object" ? m.settings : {},
      summary: s, file: fileName, bytes: xml.length,
    };
    try { await putBoth(work, { slug: m.slug, xml, stamp, timeline, timelineStamp: timeline ? stamp : "" }); }
    catch (e) { throw new Error(e?.name === "QuotaExceededError" ? "瀏覽器的儲存空間不夠：請刪掉不用的作品再試" : `存不進瀏覽器：${e.message || e}`); }
    navigator.storage?.persist?.().catch(() => {});   // 請瀏覽器不要在空間緊時自動清掉
    return {
      ...s, slug: m.slug, work: m.work, import: null, pieces: [], replaced: old ? 1 : 0, renamed_from: "", added_to_existing: false,
      parts: s.parts || work.parts.map((p) => p.part), files_used: s.files_used?.length ? s.files_used : [fileName],
      files_unused: s.files_unused || 0, report: s.report || [], problems: s.problems || 0, config_file: s.config_file || "",
    };
  }

  // ---------- /api/... 的回答 ----------
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
  const fail = (status, detail) => json({ detail }, status);
  const partInfo = (p) => ({ id: p.xml_id, xmlId: p.xml_id, part: p.part, name: p.part, measures: p.measures, status: { state: "done" }, pages: [], import: null });
  const workInfo = (w) => ({ slug: w.slug, work: w.work, created: w.created, edited: !!w.edited, parts: w.parts.map(partInfo), ready: true });
  const relative = (path) => new URL(path, document.baseURI).href;
  async function examples() {
    try { const r = await fetch(relative("examples/index.json"), { cache: "no-cache" }); return r.ok ? await r.json() : []; } catch { return []; }
  }
  async function bodyText(opts) { const b = opts?.body; return b == null ? "" : typeof b === "string" ? b : await new Response(b).text(); }

  async function hostedFetch(url, opts = {}) {
    const u = new URL(url, location.href), method = (opts.method || "GET").toUpperCase();
    const m = /\/api\/(.*)$/.exec(u.pathname);
    if (!m) return fetch(url, opts);
    const path = m[1].split("/").map(decodeURIComponent);
    try {
      if (path[0] === "works" && path.length === 1 && method === "GET")
        return json((await getAll("works")).sort((a, b) => b.created - a.created).map(workInfo));
      if (path[0] === "works" && path.length >= 2) {
        const slug = path[1], w = await get("works", slug);
        if (!w) return fail(404, "work not found");
        const what = path.slice(2).join("/");
        if (!what && method === "GET") return json(workInfo(w));
        if (!what && method === "DELETE") { await remove(slug); return json({ ok: true }); }
        if (what === "settings" && method === "GET") return json(w.settings || {});
        if (what === "settings" && method === "PUT") { await put("works", { ...w, settings: JSON.parse(await bodyText(opts)) }); return json({ ok: true }); }
        const sc = await get("scores", slug);
        if (!sc) return fail(409, "樂譜不見了：請再匯入一次");
        if (what === "score.musicxml" && method === "GET")
          return new Response(sc.xml, { headers: { "content-type": "application/vnd.recordare.musicxml+xml", "x-score-stamp": sc.stamp } });
        if (what === "score.musicxml" && method === "PUT") {   // 修正音符：原樣存下合併譜；時間軸作廢，下次打開時瀏覽器重新分析
          const xml = await bodyText(opts);
          if (!xml.slice(0, 4000).includes("<score-partwise")) return fail(400, "not MusicXML (score-partwise)");
          await putBoth({ ...w, edited: true, bytes: xml.length }, { ...sc, xml, stamp: newStamp(), timeline: null, timelineStamp: "" });
          return json({ ok: true, updated: [] });
        }
        if (what === "timeline.json" && method === "GET")
          return sc.timeline ? new Response(JSON.stringify(sc.timeline), { headers: { "content-type": "application/json", "x-score-stamp": sc.timelineStamp } }) : fail(404, "no timeline");
        if (what === "timeline.json" && method === "PUT") {   // 瀏覽器核對過（或自己分析）的時間軸：版本戳對得上才收
          if (u.searchParams.get("stamp") !== sc.stamp) return fail(409, "score changed since the timeline was computed");
          const tl = JSON.parse(await bodyText(opts));
          if (!tl || !Array.isArray(tl.steps) || !Number.isInteger(tl.nMeasures)) return fail(400, "格式不對");
          await put("scores", { ...sc, timeline: { ...tl, verified: true }, timelineStamp: sc.stamp });
          return json({ ok: true });
        }
      }
      if (path[0] === "examples" && path.length === 1 && method === "GET") return json(await examples());
      return fail(404, LOCAL_ONLY);
    } catch (e) { return fail(500, String(e?.message || e)); }
  }

  // app.js 的匯入（取代 XHR 上傳）：「＋ 匯入樂譜」／拖進來的檔案，或內建範例
  async function importRequest(url, body, prog) {
    const ex = /\/api\/examples\/([^/]+)\/import$/.exec(url);
    if (ex) {
      const id = decodeURIComponent(ex[1]);
      if (!(await examples()).some((e) => e.id === id)) throw new Error(`沒有這個範例：${id}`);
      prog.set("upload", 0, 1);
      const r = await fetch(relative(`examples/${encodeURIComponent(id)}.zip`));
      if (!r.ok) throw new Error(`讀不到範例：${r.status}`);
      return importPackage(`${id}.zip`, await r.arrayBuffer(), prog);
    }
    if (!/\/api\/imports$/.test(url) || !(body instanceof FormData)) throw new Error(LOCAL_ONLY);
    const files = body.getAll("files");
    if (files.length !== 1 || !/\.zip$/i.test(files[0].name)) throw new Error("網頁版一次匯入一個樂譜包（ZIP）");
    return importPackage(files[0].name, await files[0].arrayBuffer(), prog);
  }

  window.SPHosted = { fetch: hostedFetch, importRequest, list: () => getAll("works"), remove, unzip };
})();
