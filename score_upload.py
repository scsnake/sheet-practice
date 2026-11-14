"""網頁上傳 MusicXML / MXL / ZIP 總譜：先暫存、列出聲部讓使用者選，再交給 import_parts 匯入。

流程
  1. stage()       收下檔案（ZIP 只取裡面的 MusicXML / MXL），存到 <DATA_DIR>/_uploads/<token>/，回傳摘要
  2. summary()     每個檔案的聲部、猜測的作品名稱與授權，以及「聲部 → 出現在哪些檔案」
  3. run_import()  依使用者選的檔案順序（＝樂章順序）與聲部，逐一呼叫 import_parts.import_part，再跑 prepare 檢查
  4. discard()     取消時刪掉暫存

一個聲部只從「有這個聲部的檔案」抽出來，所以兩種常見的資料夾都能用：
  每個樂章一個總譜檔（每個聲部出現在所有檔案）、每個樂章每個樂器一個檔（每個聲部只出現在自己的檔案）。

ZIP 裡的路徑不可信任：檔案一律用流水號重新命名，不照 ZIP 裡的路徑寫入；解壓大小有上限。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import import_parts
import omr
import prepare
import works
import xmlsafe

STAGE_DIR = omr.DATA_DIR / "_uploads"   # 「_」開頭：works.all_pieces() 不會把它當成分譜
MAX_FILES = 300                         # 一次上傳最多幾個樂譜檔
MAX_TOTAL_BYTES = 1024 * 1024 * 1024    # 一次上傳解壓後的總大小上限
STALE_SECONDS = 6 * 3600                # 沒有匯入也沒有取消的暫存，過這麼久就刪掉
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _natural_key(label: str):
    """mvt2 排在 mvt10 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", label)]


def _safe_name(label: str) -> str:
    stem = re.sub(r"[^\w.\-]+", "_", PurePosixPath(label).stem, flags=re.UNICODE).strip("._") or "score"
    return stem[:60] + PurePosixPath(label).suffix.lower()


def _stage_path(token: str) -> Path:
    if not TOKEN_RE.match(token or ""):
        raise ValueError("上傳代碼不正確")
    d = STAGE_DIR / token
    if not (d / "manifest.json").is_file():
        raise ValueError("找不到這次上傳（可能已經匯入、取消或過期），請重新上傳")
    return d


def cleanup_stale(now: float | None = None) -> None:
    if not STAGE_DIR.is_dir():
        return
    now = time.time() if now is None else now
    for d in STAGE_DIR.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > STALE_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
        except FileNotFoundError:
            pass


def stage(uploads: list[tuple[str, BinaryIO]]) -> dict:
    """收下上傳的檔案。uploads 是 (原始檔名, 可讀的檔案物件)。檔案類型或大小不對時丟 ValueError。"""
    cleanup_stale()
    token = uuid.uuid4().hex
    d = STAGE_DIR / token
    d.mkdir(parents=True)
    files: list[dict] = []
    skipped: list[str] = []
    total = 0

    def keep(label: str, data: bytes) -> None:
        nonlocal total
        if len(files) >= MAX_FILES:
            raise ValueError(f"樂譜檔超過 {MAX_FILES} 個")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"解壓後總大小超過 {MAX_TOTAL_BYTES // (1024 * 1024)} MB")
        stored = f"{len(files) + 1:03d}-{_safe_name(label)}"
        (d / stored).write_bytes(data)
        files.append({"id": stored, "label": label})

    try:
        for name, stream in uploads:
            name = PurePosixPath((name or "").replace("\\", "/")).name
            ext = PurePosixPath(name).suffix.lower()
            if ext == ".zip":
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
                        if hidden or PurePosixPath(inner).suffix.lower() not in omr.XML_EXTS:
                            skipped.append(f"{name}: {inner}")
                            continue
                        try:
                            with z.open(info) as f:
                                data = xmlsafe.read_limited(f, xmlsafe.MAX_XML_BYTES, inner)
                        except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as e:  # 加密、不支援的壓縮法
                            raise ValueError(f"{name}: {inner} 無法解壓（{e}）") from None
                        keep(f"{name}/{inner}", data)
            elif ext in omr.XML_EXTS:
                keep(name, xmlsafe.read_limited(stream, xmlsafe.MAX_XML_BYTES, name))
            else:
                raise ValueError(f"不支援的檔案類型：{name}（可以用 .musicxml、.xml、.mxl、.zip）")
        if not files:
            raise ValueError("沒有找到 MusicXML／MXL 檔案" + (f"（略過了 {len(skipped)} 個其他檔案）" if skipped else ""))
        files.sort(key=lambda f: _natural_key(f["label"]))
        (d / "manifest.json").write_text(json.dumps(
            {"created": time.time(), "files": files, "skipped": skipped}, ensure_ascii=False))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return summary(token)


def _read_file(d: Path, f: dict) -> dict:
    """一個暫存檔的聲部與標題；讀不了時寫進 error。"""
    try:
        root = import_parts.load_root(d / f["id"])
    except Exception as e:  # noqa: BLE001 - 壞檔回報給使用者，不中斷其他檔
        return {**f, "parts": [], "error": f"無法讀取：{e}"}
    names = [n for _, n in import_parts.part_names(root)]
    title = (root.findtext("work/work-title") or root.findtext("movement-title") or "").strip()
    return {**f, "parts": names, "title": title, "provenance": omr.provenance(root), "error": ""}


def _guess_display(name: str) -> str | None:
    for pat, disp, _order in import_parts.STRING_PARTS:
        if re.fullmatch(pat, name.strip(), flags=re.IGNORECASE):
            return disp
    return None


def summary(token: str) -> dict:
    d = _stage_path(token)
    manifest = json.loads((d / "manifest.json").read_text())
    files = [_read_file(d, f) for f in manifest["files"]]
    parts: dict[str, dict] = {}
    for f in files:
        seen = set()
        for n in f["parts"]:
            p = parts.setdefault(n, {"name": n, "files": [], "duplicate": False})
            if n in seen:
                p["duplicate"] = True   # 同一個檔裡有兩個同名聲部：只會抽第一個
                continue
            seen.add(n)
            p["files"].append(f["id"])
    most = max((len(p["files"]) for p in parts.values()), default=0)
    for p in parts.values():
        guess = _guess_display(p["name"])
        p["display"] = guess or p["name"]
        p["selected"] = len(parts) == 1 or (guess is not None and len(p["files"]) == most)
    usable = [f for f in files if not f["error"]]
    work = next((f["title"] for f in usable if f["title"]), "")
    out = {
        "token": token,
        "files": [{**{k: f.get(k) for k in ("id", "label", "parts", "error")},
                   "rights": (f.get("provenance") or {}).get("rights", ""),
                   "source": (f.get("provenance") or {}).get("source", "")} for f in files],
        "parts": list(parts.values()),
        "work": work,
        "skipped": manifest.get("skipped", []),
    }
    for k in ("rights", "source"):
        value, conflict = file_provenance([f for f in out["files"] if not f["error"]], k)
        out[k], out[f"{k}_conflict"] = value, conflict
    return out


def file_provenance(files: list[dict], key: str) -> tuple[str, bool]:
    """所有檔案寫的授權（或來源）都一樣時回傳那個值；不一樣（包括有的檔案沒寫）時回傳 ("", True)。

    不一樣時不能只拿第一個檔案的值套到全部聲部：別的樂章可能是另一個來源、另一種授權。
    """
    values = {f.get(key) or "" for f in files}
    if len(values) > 1:
        return "", True
    return (values.pop() if values else ""), False


def run_import(token: str, work: str, order: list[str], parts: list[dict], rights: str = "", source: str = "") -> dict:
    """依 order（檔案 id，＝樂章順序）匯入選的聲部。parts 是 [{"name": 總譜裡的名稱, "display": 匯入後的名稱}]。"""
    d = _stage_path(token)
    info = summary(token)
    by_id = {f["id"]: f for f in info["files"] if not f["error"]}
    work = (work or "").strip()
    if not work:
        raise ValueError("請填作品名稱")
    if not order or len(set(order)) != len(order) or any(o not in by_id for o in order):
        raise ValueError("檔案順序不正確")
    if not parts:
        raise ValueError("請至少選一個聲部")
    displays = [(p.get("display") or "").strip() for p in parts]
    if any(not x for x in displays):
        raise ValueError("匯入後的聲部名稱不可以空白")
    if len({x.lower() for x in displays}) != len(displays):
        raise ValueError("匯入後的聲部名稱重複了")
    user = {"rights": (rights or "").strip(), "source": (source or "").strip()}
    for k, label in (("rights", "授權"), ("source", "來源")):
        _, conflict = file_provenance([by_id[o] for o in order], k)
        if conflict and not user[k]:
            raise ValueError(f"各檔案寫的{label}不一樣，請自己填一個值（或分開匯入）")
    plan = []
    for p, disp in zip(parts, displays):
        files = [d / o for o in order if p.get("name") in by_id[o]["parts"]]
        if not files:
            raise ValueError(f"選的檔案裡沒有聲部 {p.get('name')!r}")
        plan.append((p["name"], disp, files))

    existing = works.pieces_of(works.slug(work))
    base = max((m.get("order", 0) for _, m in existing), default=0) + 1 if existing else 1
    prov = {k: v for k, v in user.items() if v}
    created: list[str] = []
    report: list[str] = []
    try:
        for i, (name, disp, files) in enumerate(plan):
            # 用完全相同的名稱比對（re.escape），不讓總譜裡的名稱被當成正規表示式
            created.append(import_parts.import_part(work, disp, files, re.escape(name), base + i, prov))
        problems = prepare.check_work(works.slug(work), fix=True, out=report.append)
    except Exception:
        for pid in created:  # 不留下半套的作品；暫存留著，可以修正後再按一次匯入
            shutil.rmtree(omr.piece_dir(pid), ignore_errors=True)
        raise
    shutil.rmtree(d, ignore_errors=True)
    return {"slug": works.slug(work), "work": work, "pieces": created, "problems": problems, "report": report,
            "added_to_existing": bool(existing)}


def discard(token: str) -> None:
    shutil.rmtree(_stage_path(token), ignore_errors=True)
