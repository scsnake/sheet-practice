"""PDF / 圖片 → MusicXML 的辨識流程（使用 homr 進行光學樂譜辨識）。

每個「曲目」存放在 data/<piece_id>/ 底下：
  source.<ext>       原始上傳檔
  pages/NNN.png      由 PDF 轉出的頁面影像（或原始圖片）
  score.musicxml     合併後的樂譜（可被前端編輯覆寫）
  status.json        辨識進度 {state, done, total, error}
  meta.json          {name, created}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from lxml import etree

from xmlsafe import PARSER

# 資料夾可以放在 repo 外面：export SP_DATA_DIR=~/my-scores（預設是 repo 裡的 data/）
DATA_DIR = Path(os.environ.get("SP_DATA_DIR") or Path(__file__).parent / "data").expanduser().resolve()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
XML_EXTS = {".musicxml", ".xml", ".mxl"}
RENDER_DPI = 200


def slugify(name: str) -> str:
    base = re.sub(r"[^\w\-]+", "-", Path(name).stem, flags=re.UNICODE).strip("-")
    return (base[:40] or "piece") + "-" + time.strftime("%Y%m%d-%H%M%S")


def piece_dir(piece_id: str) -> Path:
    return DATA_DIR / piece_id


def write_status(pdir: Path, **kw) -> None:
    (pdir / "status.json").write_text(json.dumps(kw, ensure_ascii=False))


def read_status(pdir: Path) -> dict:
    try:
        return json.loads((pdir / "status.json").read_text())
    except FileNotFoundError:
        return {"state": "unknown"}


def create_piece(
    src: Path, display_name: str | None = None, work: str | None = None, part: str | None = None
) -> str:
    """建立曲目目錄並複製原始檔；回傳 piece_id。work/part 用來把多個分譜歸為同一作品。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    base = pid = slugify(src.name)
    n = 1
    while piece_dir(pid).exists():  # 同一秒匯入兩個同名聲部
        n += 1
        pid = f"{base}-{n}"
    pdir = piece_dir(pid)
    pdir.mkdir()
    ext = src.suffix.lower()
    shutil.copy(src, pdir / f"source{ext}")
    (pdir / "meta.json").write_text(
        json.dumps(
            {
                "name": display_name or src.stem,
                "work": (work or "").strip() or (display_name or src.stem),
                "part": (part or "").strip() or (display_name or src.stem),
                "order": int(time.time()),
                "created": time.time(),
            },
            ensure_ascii=False,
        )
    )
    write_status(pdir, state="queued", done=0, total=0)
    return pid


def pdf_to_pages(pdf: Path, out_dir: Path, dpi: int = RENDER_DPI) -> list[Path]:
    import pymupdf  # noqa: WPS433 (lazy import: heavy)

    out_dir.mkdir(exist_ok=True)
    pages = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
            p = out_dir / f"{i + 1:03d}.png"
            pix.save(p)
            pages.append(p)
    return pages


def prepare_pages(pdir: Path) -> list[Path]:
    src = next(p for p in pdir.iterdir() if p.stem == "source")
    pages_dir = pdir / "pages"
    if src.suffix.lower() == ".pdf":
        return pdf_to_pages(src, pages_dir)
    pages_dir.mkdir(exist_ok=True)
    dst = pages_dir / "001.png"
    if src.suffix.lower() == ".png":
        shutil.copy(src, dst)
    else:
        from PIL import Image

        Image.open(src).convert("RGB").save(dst)
    return [dst]


def homr_executable() -> str:
    exe = Path(sys.executable).parent / "homr"
    if exe.exists():
        return str(exe)
    found = shutil.which("homr")
    if not found:
        raise RuntimeError("找不到 homr 執行檔，請先執行 `uv sync`")
    return found


def run_homr(image: Path) -> Path:
    """對單張頁面影像執行 homr，回傳產生的 .musicxml 路徑。"""
    env = dict(os.environ, PYTHONWARNINGS="ignore")
    proc = subprocess.run(
        [homr_executable(), str(image)], capture_output=True, text=True, env=env, timeout=1800
    )
    out = image.with_suffix(".musicxml")
    if proc.returncode != 0 or not out.exists():
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"homr 辨識 {image.name} 失敗：\n{tail}")
    # homr 會順便輸出 *_teaser.png 的偵錯圖，保留給使用者對照
    return out


def _measure_nominal(measure, state: dict) -> int:
    """依 attributes（可能有多個區塊）更新 divisions／拍號，回傳小節應有的 division 數。"""
    for attrs in measure.findall("attributes"):
        d = attrs.find("divisions")
        if d is not None and d.text and d.text.strip().isdigit():
            state["div"] = int(d.text)
        t = attrs.find("time")
        if t is not None and t.find("beats") is not None and t.find("beat-type") is not None:
            try:
                state["beats"], state["beat_type"] = int(t.find("beats").text), int(t.find("beat-type").text)
            except (TypeError, ValueError):
                pass
    return int(round(state["div"] * 4 * state["beats"] / state["beat_type"]))


def sanitize_part(part) -> int:
    """修掉會讓 OSMD 崩潰的 OMR 產物：和弦裡的休止符、接在休止符後面的 <chord/>。回傳修正數。"""
    fixed = 0
    state = {"div": 1, "beats": 4, "beat_type": 4}
    for m in part.findall("measure"):
        nominal = _measure_nominal(m, state)
        notes = m.findall("note")
        # 整小節只有一個休止符（沒有音符、沒有 backup）→ 視為整小節休止，時值用拍號算（homr 會把它寫成固定 4 拍的全休止符）
        if len(notes) == 1 and notes[0].find("rest") is not None and m.find("backup") is None:
            rest, dur = notes[0].find("rest"), notes[0].find("duration")
            if dur is not None and dur.text != str(nominal):
                dur.text = str(nominal)
                rest.set("measure", "yes")
                t = notes[0].find("type")
                if t is not None:
                    notes[0].remove(t)
                fixed += 1
        prev = None
        for n in notes:
            chord = n.find("chord")
            if chord is not None and n.find("rest") is not None:
                m.remove(n)
                fixed += 1
                continue
            if chord is not None and (prev is None or prev.find("rest") is not None):
                n.remove(chord)
                fixed += 1
            prev = n
    return fixed


def merge_musicxml(files: list[Path]) -> bytes:
    """把多頁（多個 MusicXML）合併成單一 part：後續頁面的小節接在第一頁之後。"""
    root = etree.parse(str(files[0]), PARSER).getroot()
    parts = root.findall("part")
    if not parts:
        raise RuntimeError("第一頁沒有辨識到任何譜表")
    main_part = parts[0]
    for extra in parts[1:]:  # 單一聲部練習：只保留第一個 part
        root.remove(extra)
    for sp in root.findall("part-list/score-part")[1:]:
        sp.getparent().remove(sp)

    for f in files[1:]:
        other = etree.parse(str(f), PARSER).getroot()
        oparts = other.findall("part")
        if not oparts:
            continue
        for m in oparts[0].findall("measure"):
            main_part.append(m)

    # 重新編號小節、統一 divisions（各頁可能不同）
    sanitize_part(main_part)
    _unify_divisions(main_part)
    for i, m in enumerate(main_part.findall("measure"), start=1):
        m.set("number", str(i))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _unify_divisions(part) -> None:
    from math import lcm

    divs = [int(d.text) for d in part.iter("divisions") if d.text and d.text.isdigit()]
    if len(set(divs)) <= 1:
        return
    target = lcm(*divs)
    current = None
    for m in part.findall("measure"):
        d = m.find("attributes/divisions")
        if d is not None:
            current = int(d.text)
            d.text = str(target)
        if current is None:
            continue
        k = target // current
        for el in m.iter("duration"):
            if el.text and el.text.strip().lstrip("-").isdigit():
                el.text = str(int(el.text) * k)


def provenance(root) -> dict:
    """MusicXML <identification> 裡的授權與來源，例如 {"rights": "CC0-1.0", "source": "https://…"}。沒寫的欄位不回傳。"""
    ident = root.find("identification")
    if ident is None:
        return {}
    rights = "; ".join((r.text or "").strip() for r in ident.findall("rights") if (r.text or "").strip())
    source = (ident.findtext("source") or "").strip()
    return {k: v for k, v in (("rights", rights), ("source", source)) if v}


def record_provenance(pdir: Path, info: dict) -> None:
    """把授權與來源記進 meta.json；已經有的值（使用者指定的）不覆蓋。"""
    meta_path = pdir / "meta.json"
    meta = json.loads(meta_path.read_text())
    for k, v in info.items():
        if v and not meta.get(k):
            meta[k] = v
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))


def import_xml_source(pdir: Path) -> None:
    """直接上傳 MusicXML / MXL 時：解壓（若需要）並存為 score.musicxml。"""
    src = next(p for p in pdir.iterdir() if p.stem == "source")
    if src.suffix.lower() == ".mxl":
        with zipfile.ZipFile(src) as z:
            container = etree.fromstring(z.read("META-INF/container.xml"), PARSER)
            rootfile = container.find(".//rootfile").get("full-path")
            data = z.read(rootfile)
    else:
        data = src.read_bytes()
    (pdir / "score.musicxml").write_bytes(data)
    record_provenance(pdir, provenance(etree.fromstring(data, PARSER)))
    write_status(pdir, state="done", done=1, total=1)


def process_piece(piece_id: str) -> None:
    """完整流程：分頁 → 逐頁 homr → 合併。進度寫入 status.json。"""
    pdir = piece_dir(piece_id)
    try:
        src = next(p for p in pdir.iterdir() if p.stem == "source")
        if src.suffix.lower() in XML_EXTS:
            import_xml_source(pdir)
            return
        write_status(pdir, state="rendering", done=0, total=0)
        pages = prepare_pages(pdir)
        xmls: list[Path] = []
        for i, page in enumerate(pages):
            write_status(pdir, state="recognizing", done=i, total=len(pages), page=page.name)
            xmls.append(run_homr(page))
        write_status(pdir, state="merging", done=len(pages), total=len(pages))
        (pdir / "score.musicxml").write_bytes(merge_musicxml(xmls))
        write_status(pdir, state="done", done=len(pages), total=len(pages))
    except Exception as e:  # noqa: BLE001 - 進度回報給前端
        write_status(pdir, state="error", error=str(e))
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：uv run python omr.py <sheet.pdf|image|musicxml> [作品名稱] [聲部名稱]")
        sys.exit(1)
    pid = create_piece(Path(sys.argv[1]), work=sys.argv[2] if len(sys.argv) > 2 else None, part=sys.argv[3] if len(sys.argv) > 3 else None)
    print("piece id:", pid)
    process_piece(pid)
    print("完成：", piece_dir(pid) / "score.musicxml")
