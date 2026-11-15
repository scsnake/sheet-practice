"""本機網頁伺服器：上傳分譜、查詢辨識進度、作品（多聲部）合併與寫回、練習設定。

啟動：uv run uvicorn server:app --port 8777
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

import omr
import score_upload
import works

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
ALLOWED = {".pdf"} | omr.IMAGE_EXTS | omr.XML_EXTS

app = FastAPI(title="sheet-practice")
app.add_middleware(GZipMiddleware, minimum_size=4096)


def _pdir(piece_id: str) -> Path:
    if "/" in piece_id or piece_id.startswith("."):
        raise HTTPException(400, "bad id")
    p = omr.piece_dir(piece_id)
    if not p.is_dir():
        raise HTTPException(404, "piece not found")
    return p


def _safe_process(pid: str) -> None:
    try:
        omr.process_piece(pid)
    except Exception as e:  # noqa: BLE001
        print(f"[omr] {pid} failed: {e}")


# ---------- 作品 ----------

@app.get("/api/works")
def list_works():
    return works.list_works()


@app.get("/api/works/{slug}")
def get_work(slug: str):
    info = works.work_info(slug)
    if not info:
        raise HTTPException(404, "work not found")
    return info


@app.get("/api/works/{slug}/score.musicxml")
def get_work_score(slug: str):
    if not works.pieces_of(slug):
        raise HTTPException(404, "work not found")
    try:
        data, stamp = works.merged_cached(slug)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(500, str(e))
    return Response(data, media_type="application/vnd.recordare.musicxml+xml",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.musicxml"', "ETag": stamp, "X-Score-Stamp": stamp})


@app.get("/api/works/{slug}/timeline.json")
def get_work_timeline(slug: str):
    if not works.pieces_of(slug):
        raise HTTPException(404, "work not found")
    try:
        data, stamp = works.timeline_cached(slug)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    return Response(data, media_type="application/json", headers={"ETag": stamp, "X-Score-Stamp": stamp})


@app.put("/api/works/{slug}/timeline.json")
async def put_work_timeline(slug: str, stamp: str, request: Request):
    if not works.pieces_of(slug):
        raise HTTPException(404, "work not found")
    try:
        ok = works.store_verified_timeline(slug, stamp, await request.body())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(409, "score changed since the timeline was computed")
    return {"ok": True}


@app.put("/api/works/{slug}/score.musicxml")
async def put_work_score(slug: str, request: Request):
    body = await request.body()
    if b"<score-partwise" not in body[:4000]:
        raise HTTPException(400, "not MusicXML (score-partwise)")
    try:
        updated = works.split_work(slug, body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"寫回失敗：{e}")
    return {"ok": True, "updated": updated}


@app.get("/api/works/{slug}/settings")
def get_work_settings(slug: str):
    return JSONResponse(works.read_settings(slug))


@app.put("/api/works/{slug}/settings")
async def put_work_settings(slug: str, request: Request):
    works.write_settings(slug, await request.json())
    return {"ok": True}


# ---------- 分譜 ----------

@app.get("/api/pieces")
def list_pieces():
    return [works.piece_info(p, m) for p, m in works.all_pieces()]


@app.post("/api/pieces")
async def upload_piece(file: UploadFile, work: str = Form(""), part: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"不支援的檔案類型：{ext}")
    omr.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = omr.DATA_DIR / f".upload-{time.time_ns()}{ext}"
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tmp_named = tmp.with_name(Path(file.filename).name)
    tmp.rename(tmp_named)
    try:
        pid = omr.create_piece(tmp_named, work=work, part=part)
    finally:
        tmp_named.unlink(missing_ok=True)
    threading.Thread(target=_safe_process, args=(pid,), daemon=True).start()
    meta = works.read_meta(omr.piece_dir(pid))
    return {"id": pid, "work": meta["work"], "slug": works.slug(meta["work"])}


@app.patch("/api/pieces/{piece_id}")
async def patch_piece(piece_id: str, request: Request):
    p = _pdir(piece_id)
    data = await request.json()
    meta = works.read_meta(p)
    for k in ("work", "part", "name"):
        if k in data and str(data[k]).strip():
            meta[k] = str(data[k]).strip()
    if "order" in data:
        meta["order"] = int(data["order"])
    (p / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return works.piece_info(p, meta)


@app.post("/api/pieces/{piece_id}/reprocess")
def reprocess(piece_id: str):
    p = _pdir(piece_id)
    shutil.rmtree(p / "pages", ignore_errors=True)
    threading.Thread(target=_safe_process, args=(piece_id,), daemon=True).start()
    return {"ok": True}


@app.get("/api/pieces/{piece_id}")
def piece(piece_id: str):
    p = _pdir(piece_id)
    return works.piece_info(p, works.read_meta(p))


@app.delete("/api/pieces/{piece_id}")
def delete_piece(piece_id: str):
    p = _pdir(piece_id)
    import_id = works.read_meta(p).get("import")
    shutil.rmtree(p)
    score_upload.remove_if_unused(import_id)   # 這筆匯入的聲部都刪光了：原始檔也不必留
    return {"ok": True}


@app.get("/api/pieces/{piece_id}/score.musicxml")
def get_score(piece_id: str):
    f = _pdir(piece_id) / "score.musicxml"
    if not f.exists():
        raise HTTPException(404, "score not ready")
    return FileResponse(f, media_type="application/vnd.recordare.musicxml+xml")


@app.put("/api/pieces/{piece_id}/score.musicxml")
async def put_score(piece_id: str, request: Request):
    p = _pdir(piece_id)
    body = await request.body()
    if b"<score-partwise" not in body[:4000]:
        raise HTTPException(400, "not MusicXML")
    works.write_piece_score(p, body)
    return {"ok": True}


@app.get("/api/pieces/{piece_id}/pages/{name}")
def page_image(piece_id: str, name: str):
    f = _pdir(piece_id) / "pages" / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f)


# ---------- 總譜匯入（MusicXML / MXL / ZIP：預設值直接匯入，之後在「匯入設定」裡調整） ----------


@app.post("/api/imports")
def create_import(files: list[UploadFile]):
    """收下一個或多個 MusicXML / MXL / ZIP，用預設設定（或 ZIP 裡的 sheet-practice.json）直接匯入。"""
    try:
        return score_upload.create([(f.filename or "", f.file) for f in files])
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/imports/{import_id}")
def get_import(import_id: str):
    try:
        return score_upload.summary(import_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


class ImportPart(BaseModel):
    name: str
    display: str


class ImportPlan(BaseModel):
    work: str
    files: list[str]
    parts: list[ImportPart]
    rights: str = ""
    source: str = ""
    practice: str = ""


@app.post("/api/imports/{import_id}/apply")
def apply_import(import_id: str, plan: ImportPlan):
    """用改過的設定重新匯入；這筆匯入原本的聲部會被換掉。"""
    try:
        return score_upload.apply(import_id, plan.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/examples")
def list_examples():
    """repo 裡附的範例樂曲（授權允許自由散布）。"""
    return score_upload.examples()


@app.post("/api/examples/{example_id}/import")
def import_example(example_id: str):
    try:
        return score_upload.import_example(example_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/imports/{import_id}/config")
def import_config(import_id: str):
    """目前的匯入設定，存成 sheet-practice.json：放進 ZIP，下次匯入就直接用這些設定。"""
    try:
        cfg = score_upload.export_config(import_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return Response(json.dumps(cfg, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{score_upload.CONFIG_NAME}"'})


@app.get("/")
def index():
    return Response((STATIC / "index.html").read_bytes(), media_type="text/html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
