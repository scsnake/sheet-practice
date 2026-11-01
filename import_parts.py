"""從現成的 MusicXML / MXL 總譜（每個樂章一個檔）抽出弦樂分譜，匯入成作品，不經過 OMR。

用法：
  uv run python import_parts.py "<作品名稱>" mvt1.mxl mvt2.mxl [...]            # 自動抽出弦樂五部
  uv run python import_parts.py "<作品名稱>" score.mxl --part "Violin 2=Violin II"  # 指定 part-name=顯示名稱
  uv run python import_parts.py --list score.mxl                                # 列出總譜裡的聲部
  uv run python import_parts.py "<作品名稱>" score.mxl --rights "CC0-1.0" --source "https://…"  # 記錄授權與來源

多個檔案視為連續樂章，依序接成一份譜（小節重新編號、divisions 統一、樂章開頭換頁）。
速度標記通常只寫在總譜第一個聲部（長笛），會複製到抽出的分譜裡，播放才有正確速度。
"""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

import omr
from xmlsafe import PARSER

# 預設要抽的弦樂聲部：總譜 part-name 的樣式 → 顯示名稱、順序
STRING_PARTS = [
    (r"^(violin|violins|vln\.?|vn\.?)\s*(1|i)$", "Violin I", 1),
    (r"^(violin|violins|vln\.?|vn\.?)\s*(2|ii)$", "Violin II", 2),
    (r"^(viola|violas|vla\.?)$", "Viola", 3),
    (r"^(violoncello|violoncellos|cello|cellos|vc\.?|vcl\.?)$", "Cello", 4),
    (r"^(contrabass|contrabasses|double bass|basses|cb\.?|db\.?)$", "Contrabass", 5),
]


def load_root(path: Path):
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as z:
            container = etree.fromstring(z.read("META-INF/container.xml"), PARSER)
            rootfile = container.find(".//rootfile").get("full-path")
            return etree.fromstring(z.read(rootfile), PARSER)
    return etree.parse(str(path), PARSER).getroot()


def part_names(root) -> list[tuple[str, str]]:
    return [(sp.get("id"), (sp.findtext("part-name") or "").strip()) for sp in root.findall("part-list/score-part")]


def find_part(root, pattern: str):
    """依 part-name（正規表示式或完全相同）找 part 元素。"""
    for pid, name in part_names(root):
        if name == pattern or re.fullmatch(pattern, name, flags=re.IGNORECASE):
            p = root.find(f"part[@id='{pid}']")
            if p is not None:
                return p, name
    return None, None


def tempo_directions(root) -> dict[int, list]:
    """總譜裡帶 <sound tempo> 或節拍器記號的 direction，依小節序號收集（通常只在第一個 part）。"""
    found: dict[int, list] = {}
    for p in root.findall("part"):
        for i, m in enumerate(p.findall("measure")):
            for d in m.findall("direction"):
                snd = d.find("sound")
                if (snd is not None and snd.get("tempo")) or d.find("direction-type/metronome") is not None:
                    found.setdefault(i, []).append(d)
        if found:
            return found
    return found


def extract_part(root, part_el, tempos: dict[int, list]):
    """複製 part，並把總譜的速度標記塞進對應小節（若該小節還沒有）。"""
    part = copy.deepcopy(part_el)
    for i, m in enumerate(part.findall("measure")):
        if i not in tempos:
            continue
        has = any(d.find("sound") is not None and d.find("sound").get("tempo") for d in m.findall("direction"))
        if has:
            continue
        anchor = m.find("attributes")
        for d in tempos[i]:
            nd = copy.deepcopy(d)
            for w in nd.findall("direction-type/words"):  # 文字留著（Allegro…），但不要用總譜的絕對座標
                for k in ("default-x", "default-y", "relative-x", "relative-y"):
                    w.attrib.pop(k, None)
            if anchor is not None:
                anchor.addnext(nd)
                anchor = nd
            else:
                m.insert(0, nd)
    return part


def build_part_score(files: list[Path], pattern: str, display_name: str) -> bytes:
    roots = [load_root(f) for f in files]
    out = None
    main = None
    for mvt, (f, root) in enumerate(zip(files, roots), start=1):
        part_el, found_name = find_part(root, pattern)
        if part_el is None:
            raise ValueError(f"{f.name} 裡找不到聲部 {pattern!r}；有：{[n for _, n in part_names(root)]}")
        part = extract_part(root, part_el, tempo_directions(root))
        if out is None:
            out = etree.Element("score-partwise", version="4.0")
            for child in root:
                if child.tag in ("part-list", "part"):
                    continue
                out.append(copy.deepcopy(child))
            pl = etree.SubElement(out, "part-list")
            sp = etree.SubElement(pl, "score-part", id="P1")
            etree.SubElement(sp, "part-name").text = display_name
            main = etree.SubElement(out, "part", id="P1")
        measures = part.findall("measure")
        if measures:  # 標記樂章開頭（前端用來分樂章顯示；MuseScore 匯出的換頁記號太多，不能拿來當樂章邊界）
            measures[0].set("id", f"movement-{mvt}")
            if main.findall("measure"):
                pr = measures[0].find("print")
                if pr is None:
                    pr = etree.Element("print")
                    measures[0].insert(0, pr)
                pr.set("new-page", "yes")
        for m in measures:
            main.append(m)
    omr.sanitize_part(main)
    omr._unify_divisions(main)
    for i, m in enumerate(main.findall("measure"), start=1):
        m.set("number", str(i))
    return etree.tostring(out, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def import_part(work: str, display_name: str, files: list[Path], pattern: str, order: int | None = None,
                info: dict | None = None) -> str:
    data = build_part_score(files, pattern, display_name)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"{display_name.replace(' ', '_')}.musicxml"
        tmp.write_bytes(data)
        pid = omr.create_piece(tmp, display_name=display_name, work=work, part=display_name)
    if order is not None:
        meta_path = omr.piece_dir(pid) / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["order"] = order
        meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    omr.record_provenance(omr.piece_dir(pid), info or {})  # 使用者指定的值優先於檔案裡寫的
    omr.process_piece(pid)  # .musicxml → 直接存成 score.musicxml
    return pid


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv[0] == "--list":
        for f in argv[1:]:
            print(f, part_names(load_root(Path(f))))
        return
    work = argv[0]
    files: list[Path] = []
    specs: list[tuple[str, str]] = []
    info: dict[str, str] = {}
    i = 1
    while i < len(argv):
        if argv[i] == "--part":
            pat, _, name = argv[i + 1].partition("=")
            specs.append((pat, name or pat))
            i += 2
        elif argv[i] in ("--rights", "--source"):
            info[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            files.append(Path(argv[i]))
            i += 1
    if not files:
        print("請給總譜檔案")
        return
    if not specs:
        names = [n for _, n in part_names(load_root(files[0]))]
        for pat, disp, _order in STRING_PARTS:
            if any(re.fullmatch(pat, n, flags=re.IGNORECASE) for n in names):
                specs.append((pat, disp))
        if not specs:
            print("找不到弦樂聲部，總譜裡有：", names)
            return
    for order, (pat, disp) in enumerate(specs, start=1):
        pid = import_part(work, disp, files, pat, order, info)
        root = etree.parse(str(omr.piece_dir(pid) / "score.musicxml"), PARSER).getroot()
        print(f"{disp}: {pid}  小節數 {len(root.find('part').findall('measure'))}")


if __name__ == "__main__":
    main(sys.argv[1:])
