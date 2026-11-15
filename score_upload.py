"""網頁匯入 MusicXML / MXL / ZIP 總譜：選了檔案就用預設值直接匯入，細節之後在「匯入設定」裡改。

流程
  create()   收下檔案（ZIP 只取裡面的 MusicXML / MXL 與 sheet-practice.json），存成一筆「匯入紀錄」
             <DATA_DIR>/_imports/<id>/，算出預設的匯入設定，馬上匯入
  summary()  紀錄裡每個檔案的聲部、資料夾、授權，以及目前的匯入設定（給「匯入設定」視窗）
  apply()    用新的設定重新匯入：先建新聲部，成功後才刪掉這筆紀錄原本的聲部
  export_config()  目前的設定，格式和 sheet-practice.json 一樣（可以放進 ZIP，下次直接套用）

預設設定（ZIP 裡沒有 sheet-practice.json 時）
  聲部：弦樂五部（依名稱判斷）；總譜裡沒有弦樂時，全部聲部
  檔案：同一個資料夾裡的檔案才算一組樂章。選「最多檔案含有全部預設聲部」的資料夾；
        一樣多時，選每個檔案聲部最多的（＝總譜）。這樣一個作品資料夾裡同時有總譜、分部、
        各樂器分譜時，不會把同一個樂章接兩次。沒有任何檔案含全部預設聲部時（例如每個樂器一個檔），
        選涵蓋最多預設聲部的資料夾，每個聲部從有它的檔案抽
  作品名稱：檔案裡的作品標題，沒有就用檔名；已經有同名作品時加「 (2)」，不會混進別的作品
  授權／來源：各檔案一樣就用那個值；不一樣時每個檔案的值都記下來，不猜
  練習聲部：不指定（app 預設第一個聲部）；sheet-practice.json 的 practice 可以指定

ZIP 裡的路徑不可信任：檔案一律用流水號重新命名，不照 ZIP 裡的路徑寫入；解壓大小有上限。

進度：create()／apply()／import_example() 可以給 progress(stage, done, total)。stage 依序是
  read（讀取檔案 done/total）、build（建立聲部 done/total）、check（prepare.py 檢查）。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

import import_parts
import omr
import prepare
import works
import xmlsafe

IMPORTS_DIR = omr.DATA_DIR / "_imports"   # 「_」開頭：works.all_pieces() 不會把它當成分譜
CONFIG_NAME = "sheet-practice.json"
MAX_FILES = 300                            # 一次匯入最多幾個樂譜檔
MAX_TOTAL_BYTES = 1024 * 1024 * 1024       # 一次匯入解壓後的總大小上限
MAX_CONFIG_BYTES = 1024 * 1024
ORPHAN_SECONDS = 3600                      # 沒有任何聲部的匯入紀錄，過這麼久就刪掉
ID_RE = re.compile(r"^[0-9a-f]{32}$")

Progress = Callable[[str, int, int], None]


def _no_progress(stage: str, done: int, total: int) -> None:
    pass


def _natural_key(label: str):
    """mvt2 排在 mvt10 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", label)]


def _safe_name(label: str) -> str:
    stem = re.sub(r"[^\w.\-]+", "_", PurePosixPath(label).stem, flags=re.UNICODE).strip("._") or "score"
    return stem[:60] + PurePosixPath(label).suffix.lower()


def _record(import_id: str) -> Path:
    if not ID_RE.match(import_id or ""):
        raise ValueError("匯入紀錄代碼不正確")
    d = IMPORTS_DIR / import_id
    if not (d / "manifest.json").is_file():
        raise ValueError("找不到這筆匯入紀錄（可能已經刪除），請重新匯入")
    return d


def pieces_of_import(import_id: str) -> list[tuple[Path, dict]]:
    items = [(p, m) for p, m in works.all_pieces() if m.get("import") == import_id]
    items.sort(key=lambda x: (x[1]["order"], x[1].get("created", 0)))
    return items


def cleanup_orphans(now: float | None = None) -> None:
    """刪掉已經沒有任何聲部的匯入紀錄（聲部都在「管理」裡刪掉了）。剛建立的不動，可能正在匯入。"""
    if not IMPORTS_DIR.is_dir():
        return
    now = time.time() if now is None else now
    used = {m.get("import") for _, m in works.all_pieces()}
    for d in IMPORTS_DIR.iterdir():
        try:
            if d.is_dir() and d.name not in used and now - d.stat().st_mtime > ORPHAN_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
        except FileNotFoundError:
            pass


def remove_if_unused(import_id: str | None) -> None:
    if import_id and ID_RE.match(import_id) and not pieces_of_import(import_id):
        shutil.rmtree(IMPORTS_DIR / import_id, ignore_errors=True)


# ---------- 收檔 ----------


def _receive(d: Path, uploads: list[tuple[str, BinaryIO]]) -> dict:
    """把上傳的檔案存進紀錄資料夾，回傳 manifest。"""
    files: list[dict] = []
    skipped: list[str] = []
    configs: list[tuple[str, bytes]] = []   # (路徑, 內容)
    total = 0

    def keep(label: str, path: str, data: bytes) -> None:
        nonlocal total
        if len(files) >= MAX_FILES:
            raise ValueError(f"樂譜檔超過 {MAX_FILES} 個")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"解壓後總大小超過 {MAX_TOTAL_BYTES // (1024 * 1024)} MB")
        stored = f"{len(files) + 1:03d}-{_safe_name(label)}"
        (d / stored).write_bytes(data)
        files.append({"id": stored, "label": label, "path": path})

    for name, stream in uploads:
        name = PurePosixPath((name or "").replace("\\", "/")).name
        ext = PurePosixPath(name).suffix.lower()
        if name.lower() == CONFIG_NAME:
            configs.append((name, xmlsafe.read_limited(stream, MAX_CONFIG_BYTES, name)))
        elif ext == ".zip":
            try:
                z = zipfile.ZipFile(stream)
            except zipfile.BadZipFile:
                raise ValueError(f"{name} 不是正確的 ZIP 檔") from None
            with z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    inner = info.filename.replace("\\", "/")
                    parts = PurePosixPath(inner).parts
                    hidden = any(p.startswith(".") or p == "__MACOSX" for p in parts) or inner.startswith("/")
                    is_config = PurePosixPath(inner).name.lower() == CONFIG_NAME
                    if hidden or not (is_config or PurePosixPath(inner).suffix.lower() in omr.XML_EXTS):
                        skipped.append(f"{name}: {inner}")
                        continue
                    try:
                        with z.open(info) as f:
                            limit = MAX_CONFIG_BYTES if is_config else xmlsafe.MAX_XML_BYTES
                            data = xmlsafe.read_limited(f, limit, inner)
                    except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as e:  # 加密、不支援的壓縮法
                        raise ValueError(f"{name}: {inner} 無法解壓（{e}）") from None
                    if is_config:
                        configs.append((inner, data))
                    else:
                        keep(f"{name}/{inner}", inner, data)
        elif ext in omr.XML_EXTS:
            keep(name, name, xmlsafe.read_limited(stream, xmlsafe.MAX_XML_BYTES, name))
        else:
            raise ValueError(f"不支援的檔案類型：{name}（可以用 .musicxml、.xml、.mxl、.zip）")
    if not files:
        raise ValueError("沒有找到 MusicXML／MXL 檔案" + (f"（略過了 {len(skipped)} 個其他檔案）" if skipped else ""))
    files.sort(key=lambda f: _natural_key(f["label"]))
    config, config_path = None, ""
    if configs:
        configs.sort(key=lambda c: (len(PurePosixPath(c[0]).parts), c[0]))   # 最上層的那個
        config_path, raw = configs[0]
        try:
            config = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"{config_path} 不是正確的 JSON：{e}") from None
        if not isinstance(config, dict):
            raise ValueError(f"{config_path} 的內容要是一個物件（{{…}}）")
    return {"created": time.time(), "files": files, "skipped": skipped,
            "config_file": config_path, "config_in_upload": config}


def _read_file(d: Path, f: dict) -> dict:
    """一個樂譜檔的聲部與標題；讀不了時寫進 error。"""
    try:
        root = import_parts.load_root(d / f["id"])
    except Exception as e:  # noqa: BLE001 - 壞檔回報給使用者，不中斷其他檔
        return {**f, "parts": [], "title": "", "rights": "", "source": "", "error": f"無法讀取：{e}"}
    names = [n for _, n in import_parts.part_names(root)]
    title = (root.findtext("work/work-title") or root.findtext("movement-title") or "").strip()
    prov = omr.provenance(root)
    return {**f, "parts": names, "title": title, "rights": prov.get("rights", ""), "source": prov.get("source", ""),
            "error": ""}


def _folder(f: dict) -> str:
    return str(PurePosixPath(f["path"]).parent)


# ---------- 預設設定 ----------


def _string_part(name: str) -> tuple[str, int] | None:
    """弦樂聲部的標準名稱與順序（Violin I = 1 … Contrabass = 5）；不是弦樂時 None。"""
    for pat, disp, order in import_parts.STRING_PARTS:
        if re.fullmatch(pat, name.strip(), flags=re.IGNORECASE):
            return disp, order
    return None


def _guess_display(name: str) -> str | None:
    hit = _string_part(name)
    return hit[0] if hit else None


def _part_names(files: list[dict]) -> list[str]:
    out: list[str] = []
    for f in files:
        for n in f["parts"]:
            if n not in out:
                out.append(n)
    return out


def _default_targets(files: list[dict]) -> list[str]:
    names = _part_names(files)
    strings = sorted((n for n in names if _string_part(n)), key=lambda n: _string_part(n)[1])  # 各樂器一個檔時，檔名順序不是總譜順序
    return strings or names


def _auto_files(usable: list[dict]) -> list[dict]:
    """預設用哪些檔案：見模組說明「檔案」。"""
    targets = set(_default_targets(usable))
    folders: dict[str, list[dict]] = {}
    for f in usable:
        folders.setdefault(_folder(f), []).append(f)

    def score(item):
        name, fs = item
        with_any = [f for f in fs if targets & set(f["parts"])]
        full = sum(1 for f in with_any if targets <= set(f["parts"]))
        avg = sum(len(f["parts"]) for f in with_any) / len(with_any) if with_any else 0
        covered = len(targets & set(_part_names(fs)))
        # 先比「含全部預設聲部的檔案數」；都沒有時比涵蓋幾個預設聲部、檔案少的優先
        return (full, avg if full else 0, covered, -len(fs) if not full else 0)

    folder_order = sorted(folders, key=_natural_key)
    best = max(folder_order, key=lambda k: score((k, folders[k])))   # max 遇到平手取第一個
    return [f for f in folders[best] if targets & set(f["parts"])]


def _default_parts(files: list[dict]) -> list[dict]:
    out, used = [], set()
    for n in _default_targets(files):
        disp = _guess_display(n) or n
        if disp.lower() in used:   # 兩個名稱猜成同一個顯示名稱：第二個保留原名
            disp = n
        used.add(disp.lower())
        out.append({"name": n, "display": disp})
    return out


def _from_config(config: dict, config_file: str, usable: list[dict]) -> dict:
    """把 sheet-practice.json 換成內部設定（檔案 id）。路徑以設定檔所在的資料夾為準。"""
    plan: dict = {}
    base = PurePosixPath(config_file).parent
    by_path = {f["path"]: f for f in usable}
    if config.get("files") is not None:
        if not isinstance(config["files"], list) or not all(isinstance(x, str) for x in config["files"]):
            raise ValueError(f"{config_file}：files 要是檔案路徑的清單")
        ids = []
        for rel in config["files"]:
            path = str(base / rel) if str(base) != "." else rel
            f = by_path.get(path) or by_path.get(rel)
            if f is None:
                raise ValueError(f"{config_file}：找不到檔案 {rel!r}")
            ids.append(f["id"])
        plan["files"] = ids
    if config.get("parts") is not None:
        if not isinstance(config["parts"], list):
            raise ValueError(f"{config_file}：parts 要是清單")
        parts = []
        for p in config["parts"]:
            if isinstance(p, str):
                p = {"name": p}
            if not isinstance(p, dict) or not isinstance(p.get("name"), str):
                raise ValueError(f"{config_file}：parts 的每一項要有 name")
            parts.append({"name": p["name"], "display": str(p.get("display") or _guess_display(p["name"]) or p["name"])})
        plan["parts"] = parts
    for k in ("work", "rights", "source", "practice"):
        if config.get(k) is not None:
            plan[k] = str(config[k])
    return plan


def _default_plan(manifest: dict, usable: list[dict]) -> dict:
    plan = {}
    if manifest.get("config_in_upload") is not None:
        plan = _from_config(manifest["config_in_upload"], manifest["config_file"], usable)
    if "files" not in plan:
        plan["files"] = [f["id"] for f in _auto_files(usable)]
    chosen = [f for f in usable if f["id"] in plan["files"]]
    if "parts" not in plan:
        plan["parts"] = _default_parts(chosen)
    if not plan.get("work"):
        plan["work"] = next((f["title"] for f in chosen if f["title"]), "") or PurePosixPath(chosen[0]["label"]).stem
    plan.setdefault("rights", "")
    plan.setdefault("source", "")
    practice = (plan.get("practice") or "").strip()
    for p in plan["parts"]:   # 設定檔裡可以寫總譜裡的名稱（Violin 2）或匯入後的名稱（Violin II）
        if practice and practice.lower() == p["name"].lower():
            practice = p["display"]
    plan["practice"] = practice
    return plan


def combined_provenance(files: list[dict], key: str) -> str:
    """各檔案一樣時回傳那個值（可能是空字串）；不一樣時把每個檔案的值都列出來，不只拿第一個。"""
    values = [f.get(key) or "" for f in files]
    if len(set(values)) <= 1:
        return values[0] if values else ""
    return "; ".join(f"{f['path']}: {v or '（沒寫）'}" for f, v in zip(files, values))


def _free_work_name(name: str) -> str:
    if not works.pieces_of(works.slug(name)):
        return name
    n = 2
    while True:
        base = name
        # works.slug() 只留 60 個字元：名稱太長時「 (2)」會被切掉，要先把名稱縮短
        while len(base) > 1 and not works.slug(f"{base} ({n})").endswith(f"-{n}"):
            base = base[:-1].rstrip()
        candidate = f"{base} ({n})"
        if not works.pieces_of(works.slug(candidate)):
            return candidate
        n += 1


# ---------- 匯入 ----------


def create(uploads: list[tuple[str, BinaryIO]], progress: Progress | None = None) -> dict:
    """收下檔案，用預設設定（或 ZIP 裡的 sheet-practice.json）馬上匯入。失敗時不留下任何東西。"""
    cleanup_orphans()
    import_id = uuid.uuid4().hex
    d = IMPORTS_DIR / import_id
    d.mkdir(parents=True)
    try:
        manifest = _receive(d, uploads)
        (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        info = summary(import_id, progress)
        usable = [f for f in info["files"] if not f["error"]]
        if not usable:
            raise ValueError("所有樂譜檔都無法讀取：" + "；".join(f"{f['label']} {f['error']}" for f in info["files"][:3]))
        plan = _default_plan(manifest, usable)
        requested = plan["work"]
        plan["work"] = _free_work_name(requested)
        result = apply(import_id, plan, progress, info)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    result["renamed_from"] = requested if plan["work"] != requested else ""
    result["config_file"] = manifest.get("config_file", "")
    return result


def summary(import_id: str, progress: Progress | None = None) -> dict:
    progress = progress or _no_progress
    d = _record(import_id)
    manifest = json.loads((d / "manifest.json").read_text())
    files = []
    for i, f in enumerate(manifest["files"]):
        progress("read", i, len(manifest["files"]))
        files.append(_read_file(d, f))
    progress("read", len(files), len(files))
    for f in files:
        f["folder"] = _folder(f)
    parts: dict[str, dict] = {}
    for f in files:
        seen = set()
        for n in f["parts"]:
            p = parts.setdefault(n, {"name": n, "files": [], "duplicate": False, "guess": _guess_display(n) or n})
            if n in seen:
                p["duplicate"] = True   # 同一個檔裡有兩個同名聲部：只會抽第一個
                continue
            seen.add(n)
            p["files"].append(f["id"])
    plan = None
    if (d / "import.json").is_file():
        plan = json.loads((d / "import.json").read_text())
    return {
        "id": import_id,
        "files": files,
        "parts": list(parts.values()),
        "skipped": manifest.get("skipped", []),
        "config_file": manifest.get("config_file", ""),
        "plan": plan,
        "pieces": [works.piece_info(p, m) for p, m in pieces_of_import(import_id)],
    }


def _validate(plan: dict, by_id: dict) -> list[tuple[str, str, list[Path]]]:
    work = (plan.get("work") or "").strip()
    if not work:
        raise ValueError("請填作品名稱")
    order = plan.get("files") or []
    if not order or len(set(order)) != len(order) or any(o not in by_id for o in order):
        raise ValueError("檔案清單不正確")
    parts = plan.get("parts") or []
    if not parts:
        raise ValueError("請至少選一個聲部")
    displays = [(p.get("display") or "").strip() for p in parts]
    if any(not x for x in displays):
        raise ValueError("匯入後的聲部名稱不可以空白")
    if len({x.lower() for x in displays}) != len(displays):
        raise ValueError("匯入後的聲部名稱重複了")
    practice = (plan.get("practice") or "").strip()
    if practice and practice.lower() not in {x.lower() for x in displays}:
        raise ValueError(f"練習聲部 {practice!r} 不在要匯入的聲部裡")
    out = []
    for p, disp in zip(parts, displays):
        files = [o for o in order if p.get("name") in by_id[o]["parts"]]
        if not files:
            raise ValueError(f"選的檔案裡沒有聲部 {p.get('name')!r}")
        out.append((p["name"], disp, files))
    return out


def apply(import_id: str, plan: dict, progress: Progress | None = None, info: dict | None = None) -> dict:
    """用 plan 匯入（第一次匯入，或在「匯入設定」裡改過之後重新匯入）。

    plan = {"work", "files": [檔案 id，＝樂章順序], "parts": [{"name", "display"}], "rights", "source"}
    重新匯入時，先建新聲部；這筆紀錄原本的聲部先移到旁邊，檢查成功才刪，失敗就放回去。
    info：create() 剛讀過的 summary()，不必再讀一次每個檔案。
    """
    progress = progress or _no_progress
    d = _record(import_id)
    info = info or summary(import_id, progress)
    by_id = {f["id"]: f for f in info["files"] if not f["error"]}
    steps = _validate(plan, by_id)
    work = plan["work"].strip()
    order = plan["files"]
    prov = {}
    for k in ("rights", "source"):
        value = (plan.get(k) or "").strip() or combined_provenance([by_id[o] for o in order], k)
        if value:
            prov[k] = value   # 一定明寫：不寫的話會用第一個檔案的值，套到每個樂章

    old = pieces_of_import(import_id)
    others = [(p, m) for p, m in works.pieces_of(works.slug(work)) if m.get("import") != import_id]
    old_same_work = [m.get("order", 0) for p, m in old if works.slug(m["work"]) == works.slug(work)]
    if old_same_work:
        base = min(old_same_work)
    else:
        base = max((m.get("order", 0) for _, m in others), default=0) + 1 if others else 1

    created: list[str] = []
    report: list[str] = []
    aside = d / "_replaced"
    moved: list[Path] = []
    try:
        for i, (name, disp, ids) in enumerate(steps):
            progress("build", i, len(steps))
            # 用完全相同的名稱比對（re.escape），不讓總譜裡的名稱被當成正規表示式
            pid = import_parts.import_part(work, disp, [d / o for o in ids], re.escape(name), base + i, prov)
            created.append(pid)
            meta_path = omr.piece_dir(pid) / "meta.json"
            meta = json.loads(meta_path.read_text())
            meta["import"] = import_id
            meta_path.write_text(json.dumps(meta, ensure_ascii=False))
        aside.mkdir(exist_ok=True)
        for p, _ in old:
            p.rename(aside / p.name)
            moved.append(aside / p.name)
        progress("check", 0, 1)
        problems = prepare.check_work(works.slug(work), fix=True, out=report.append)
    except Exception:
        for pid in created:
            shutil.rmtree(omr.piece_dir(pid), ignore_errors=True)
        for p in moved:   # 原本的聲部放回去
            p.rename(omr.DATA_DIR / p.name)
        shutil.rmtree(aside, ignore_errors=True)
        raise
    shutil.rmtree(aside, ignore_errors=True)
    practice = (plan.get("practice") or "").strip()
    practice_xml_id = ""
    if practice:   # 練習聲部：寫進作品在伺服器上的預設設定（瀏覽器第一次打開這個作品時用）
        pid = next(pid for pid, (_, disp, _) in zip(created, steps) if disp.lower() == practice.lower())
        practice = next(disp for _, disp, _ in steps if disp.lower() == practice.lower())
        practice_xml_id = works.part_xml_id(pid)
        settings = works.read_settings(works.slug(work))
        settings["practice"] = practice_xml_id
        works.write_settings(works.slug(work), settings)
    saved = {"work": work, "files": order, "parts": [{"name": n, "display": disp} for n, disp, _ in steps],
             "rights": (plan.get("rights") or "").strip(), "source": (plan.get("source") or "").strip(),
             "practice": practice}
    (d / "import.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    first = omr.piece_dir(created[0]) / "score.musicxml"
    return {
        "import": import_id,
        "slug": works.slug(work),
        "work": work,
        "pieces": created,
        "parts": [disp for _, disp, _ in steps],
        "files_used": [by_id[o]["label"] for o in order],
        "files_unused": len(by_id) - len(order),
        "problems": problems,
        "report": report,
        "added_to_existing": bool(others),
        "replaced": len(old),
        "practice": practice,
        "practice_xml_id": practice_xml_id,
        "measures": max((works.measure_count(omr.piece_dir(pid)) or 0 for pid in created), default=0),
        "movements": first.read_text(encoding="utf-8").count('id="movement-') if first.is_file() else 0,
    }


# ---------- 內建範例（repo 裡的 examples/，只放授權允許自由散布的樂譜；見各資料夾的 SOURCE.md） ----------

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


def examples() -> list[dict]:
    out = []
    for d in sorted(EXAMPLES_DIR.iterdir()) if EXAMPLES_DIR.is_dir() else []:
        cfg_path = d / CONFIG_NAME
        if not (d.is_dir() and cfg_path.is_file()):
            continue
        cfg = json.loads(cfg_path.read_text())
        out.append({"id": d.name, "work": cfg.get("work", d.name),
                    "parts": [p["display"] if isinstance(p, dict) else p for p in cfg.get("parts", [])]})
    return out


def import_example(example_id: str, progress: Progress | None = None) -> dict:
    """把一個內建範例當成上傳的檔案匯入（和網頁匯入走同一條路）。"""
    if example_id not in {e["id"] for e in examples()}:
        raise ValueError(f"沒有這個範例：{example_id!r}")
    d = EXAMPLES_DIR / example_id
    files = sorted(f for f in d.iterdir() if f.suffix.lower() in omr.XML_EXTS or f.name == CONFIG_NAME)
    streams = [f.open("rb") for f in files]
    try:
        return create([(f.name, st) for f, st in zip(files, streams)], progress)
    finally:
        for st in streams:
            st.close()


def export_config(import_id: str) -> dict:
    """目前的設定，格式和 sheet-practice.json 一樣：路徑是相對 ZIP 最上層的路徑。"""
    info = summary(import_id)
    plan = info["plan"] or {}
    by_id = {f["id"]: f for f in info["files"]}
    return {
        "work": plan.get("work", ""),
        "files": [by_id[o]["path"] for o in plan.get("files", []) if o in by_id],
        "parts": plan.get("parts", []),
        "rights": plan.get("rights", ""),
        "source": plan.get("source", ""),
        "practice": plan.get("practice", ""),
    }
