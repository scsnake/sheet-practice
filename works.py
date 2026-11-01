"""「作品（work）」層：把多個分譜（piece）合併成一份多聲部 MusicXML，或把編輯後的合併譜拆回各分譜。

每個 piece 的 meta.json 有 work / part / order；同一 work 的 piece 依 order 排成多聲部總譜。
作品層級的設定（速度、區段、靜音…）存在 data/_works/<slug>.settings.json。
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from pathlib import Path

from lxml import etree

import omr
from xmlsafe import PARSER

WORKS_DIR = omr.DATA_DIR / "_works"


def slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip(), flags=re.UNICODE).strip("-")
    return s[:60] or "work"


def read_meta(pdir: Path) -> dict:
    try:
        return json.loads((pdir / "meta.json").read_text())
    except FileNotFoundError:
        return {}


def all_pieces() -> list[tuple[Path, dict]]:
    omr.DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in omr.DATA_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists():
            meta = read_meta(p)
            meta.setdefault("work", meta.get("name", p.name))
            meta.setdefault("part", meta.get("name", p.name))
            meta.setdefault("order", int(meta.get("created", 0)))
            out.append((p, meta))
    return out


def pieces_of(work_slug: str) -> list[tuple[Path, dict]]:
    items = [(p, m) for p, m in all_pieces() if slug(m["work"]) == work_slug]
    items.sort(key=lambda x: (x[1]["order"], x[1].get("created", 0)))
    return items


def part_xml_id(piece_id: str) -> str:
    return "P-" + re.sub(r"[^A-Za-z0-9_\-.]", "_", piece_id)


def measure_count(pdir: Path) -> int | None:
    f = pdir / "score.musicxml"
    if not f.exists():
        return None
    try:
        root = etree.parse(str(f), PARSER).getroot()
        part = root.find("part")
        return len(part.findall("measure")) if part is not None else 0
    except etree.XMLSyntaxError:
        return None


def piece_info(pdir: Path, meta: dict) -> dict:
    pages = sorted(x.name for x in (pdir / "pages").glob("[0-9][0-9][0-9].png")) if (pdir / "pages").is_dir() else []
    return {
        "id": pdir.name,
        "xmlId": part_xml_id(pdir.name),
        "name": meta.get("name", pdir.name),
        "work": meta["work"],
        "part": meta["part"],
        "order": meta["order"],
        "created": meta.get("created", 0),
        "status": omr.read_status(pdir),
        "pages": pages,
        "measures": measure_count(pdir),
    }


def work_info(work_slug: str) -> dict | None:
    items = pieces_of(work_slug)
    if not items:
        return None
    parts = [piece_info(p, m) for p, m in items]
    return {
        "slug": work_slug,
        "work": items[0][1]["work"],
        "parts": parts,
        "ready": all(pt["status"].get("state") == "done" for pt in parts),
    }


def list_works() -> list[dict]:
    groups: dict[str, list] = {}
    for p, m in all_pieces():
        groups.setdefault(slug(m["work"]), []).append((p, m))
    out = []
    for s, items in groups.items():
        items.sort(key=lambda x: (x[1]["order"], x[1].get("created", 0)))
        out.append(
            {
                "slug": s,
                "work": items[0][1]["work"],
                "created": max(m.get("created", 0) for _, m in items),
                "parts": [piece_info(p, m) for p, m in items],
            }
        )
    out.sort(key=lambda w: w["created"], reverse=True)
    return out


# ---------- 合併 ----------

def _measure_length(measure, state: dict) -> int:
    """更新 divisions / 拍號狀態並回傳該小節應有的 division 數。"""
    for attrs in measure.findall("attributes"):  # homr 可能輸出多個 attributes 區塊
        d = attrs.find("divisions")
        if d is not None and d.text and d.text.strip().isdigit():
            state["div"] = int(d.text)
        t = attrs.find("time")
        if t is not None:
            b, bt = t.find("beats"), t.find("beat-type")
            if b is not None and bt is not None:
                try:
                    state["beats"], state["beat_type"] = int(b.text), int(bt.text)
                except (TypeError, ValueError):
                    pass
    return int(round(state["div"] * 4 * state["beats"] / state["beat_type"]))


def _empty_measure(number: int, length: int, first: bool, state: dict):
    m = etree.Element("measure", number=str(number), id="pad")
    if first:
        attrs = etree.SubElement(m, "attributes")
        etree.SubElement(attrs, "divisions").text = str(state["div"])
    note = etree.SubElement(m, "note")
    etree.SubElement(note, "rest", measure="yes")
    etree.SubElement(note, "duration").text = str(max(1, length))
    return m


def merge_stamp(work_slug: str) -> str:
    """各分譜 score.musicxml 的 mtime/size 與順序 → 合併結果的版本戳（也當 ETag）。"""
    import hashlib

    h = hashlib.sha1()
    for pdir, meta in pieces_of(work_slug):
        f = pdir / "score.musicxml"
        st = f.stat() if f.exists() else None
        h.update(f"{pdir.name}|{meta.get('part')}|{meta.get('order')}|{st.st_mtime_ns if st else 0}|{st.st_size if st else 0}\n".encode())
    return h.hexdigest()[:16]


def merged_cached(work_slug: str) -> tuple[bytes, str]:
    """合併結果快取在 data/_works/<slug>.merged.musicxml；分譜沒變就直接回傳。回傳 (bytes, stamp)。"""
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = merge_stamp(work_slug)
    cache = WORKS_DIR / f"{work_slug}.merged.musicxml"
    stamp_file = WORKS_DIR / f"{work_slug}.merged.stamp"
    if cache.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return cache.read_bytes(), stamp
    data = merge_work(work_slug)
    cache.write_bytes(data)
    stamp_file.write_text(stamp)
    return data, stamp


def timeline_cached(work_slug: str) -> tuple[bytes, str]:
    """播放時間軸（JSON），跟合併譜用同一個版本戳快取。"""
    import timeline

    data, stamp = merged_cached(work_slug)
    cache = WORKS_DIR / f"{work_slug}.timeline.json"
    stamp_file = WORKS_DIR / f"{work_slug}.timeline.stamp"
    if cache.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return cache.read_bytes(), stamp
    out = timeline.compute_json(data)
    cache.write_bytes(out)
    stamp_file.write_text(stamp)
    return out, stamp


def store_verified_timeline(work_slug: str, stamp: str, body: bytes) -> bool:
    """瀏覽器用 OSMD 核對過的時間軸：版本戳對得上才收，之後直接用它。"""
    if stamp != merge_stamp(work_slug):
        return False
    obj = json.loads(body)
    if not isinstance(obj, dict) or not isinstance(obj.get("steps"), list) or not isinstance(obj.get("nMeasures"), int):
        raise ValueError("格式不對")
    obj["verified"] = True
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    (WORKS_DIR / f"{work_slug}.timeline.json").write_bytes(json.dumps(obj, separators=(",", ":")).encode())
    (WORKS_DIR / f"{work_slug}.timeline.stamp").write_text(stamp)
    return True


def merge_work(work_slug: str) -> bytes:
    items = pieces_of(work_slug)
    docs = []
    for pdir, meta in items:
        f = pdir / "score.musicxml"
        if not f.exists():
            continue  # 尚未辨識完成的聲部先略過，讓已完成的聲部可以先練
        root = etree.parse(str(f), PARSER).getroot()
        part = root.find("part")
        if part is None:
            raise ValueError(f"{meta['part']} 沒有 part")
        docs.append((pdir, meta, root, part))

    if not docs:
        raise FileNotFoundError("此作品還沒有任何辨識完成的聲部")
    base_root = docs[0][2]
    out = etree.Element("score-partwise", version="4.0")
    for child in base_root:
        if child.tag in ("part-list", "part"):
            continue
        out.append(copy.deepcopy(child))
    work_el = out.find("work")
    if work_el is None:
        work_el = etree.Element("work")
        out.insert(0, work_el)
    title = work_el.find("work-title")
    if title is None:
        title = etree.SubElement(work_el, "work-title")
    title.text = docs[0][1]["work"]

    part_list = etree.SubElement(out, "part-list")
    max_len = max(len(part.findall("measure")) for _, _, _, part in docs)
    for pdir, meta, root, part in docs:
        pid = part_xml_id(pdir.name)
        sp = etree.SubElement(part_list, "score-part", id=pid)
        etree.SubElement(sp, "part-name").text = meta["part"]
        abbr = etree.SubElement(sp, "part-abbreviation")
        abbr.text = _abbreviate(meta["part"])
        new_part = copy.deepcopy(part)
        new_part.set("id", pid)
        omr.sanitize_part(new_part)
        for pad in new_part.findall("measure[@id='pad']"):  # 舊的補齊小節不算數
            new_part.remove(pad)
        measures = new_part.findall("measure")
        state = {"div": 1, "beats": 4, "beat_type": 4}
        length = 4
        for i, m in enumerate(measures, start=1):
            length = _measure_length(m, state)
            m.set("number", str(i))
        for i in range(len(measures) + 1, max_len + 1):  # 補齊較短的聲部
            new_part.append(_empty_measure(i, length, len(measures) == 0 and i == 1, state))
        out.append(new_part)
    return etree.tostring(out, xml_declaration=True, encoding="UTF-8")  # 不縮排：檔案小一半，瀏覽器解析也快


def _abbreviate(name: str) -> str:
    n = name.lower()
    for key, ab in (("violin ii", "Vn. II"), ("violin 2", "Vn. II"), ("violin i", "Vn. I"), ("violin 1", "Vn. I"),
                    ("viola", "Vla."), ("cello", "Vc."), ("bass", "Cb."), ("violin", "Vn.")):
        if key in n:
            return ab
    return name[:6]


# ---------- 拆回 ----------

def split_work(work_slug: str, xml: bytes) -> list[str]:
    """把編輯後的多聲部 MusicXML 依 part id 寫回各 piece；回傳更新的 piece id。"""
    root = etree.fromstring(xml, PARSER)
    if root.tag != "score-partwise":
        raise ValueError("只支援 score-partwise")
    by_id = {part_xml_id(p.name): (p, m) for p, m in pieces_of(work_slug)}
    updated = []
    for part in root.findall("part"):
        pid = part.get("id")
        if pid not in by_id:
            continue
        pdir, meta = by_id[pid]
        doc = etree.Element("score-partwise", version="4.0")
        for child in root:
            if child.tag in ("part-list", "part"):
                continue
            doc.append(copy.deepcopy(child))
        pl = etree.SubElement(doc, "part-list")
        sp = root.find(f"part-list/score-part[@id='{pid}']")
        sp = copy.deepcopy(sp) if sp is not None else etree.Element("score-part", id=pid)
        sp.set("id", "P1")
        if sp.find("part-name") is None:
            etree.SubElement(sp, "part-name").text = meta["part"]
        pl.append(sp)
        new_part = copy.deepcopy(part)
        new_part.set("id", "P1")
        measures = new_part.findall("measure")
        while measures and measures[-1].get("id") == "pad" and not measures[-1].findall("note/pitch"):
            new_part.remove(measures.pop())  # 去掉合併時補齊的尾端空小節
        doc.append(new_part)
        write_piece_score(pdir, etree.tostring(doc, xml_declaration=True, encoding="UTF-8", pretty_print=True))
        updated.append(pdir.name)
    return updated


def write_piece_score(pdir: Path, data: bytes) -> None:
    f = pdir / "score.musicxml"
    if f.exists():
        backups = pdir / "backups"
        backups.mkdir(exist_ok=True)
        shutil.copy(f, backups / f"score-{time.strftime('%Y%m%d-%H%M%S')}.musicxml")
        for old in sorted(backups.glob("score-*.musicxml"))[:-20]:
            old.unlink()
    f.write_bytes(data)


# ---------- 設定 ----------

def settings_path(work_slug: str) -> Path:
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    return WORKS_DIR / f"{work_slug}.settings.json"


def read_settings(work_slug: str) -> dict:
    p = settings_path(work_slug)
    return json.loads(p.read_text()) if p.exists() else {}


def write_settings(work_slug: str, data: dict) -> None:
    settings_path(work_slug).write_text(json.dumps(data, ensure_ascii=False))
