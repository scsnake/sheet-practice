"""用掃描頁上「每行開頭的小節號碼」自動對齊 OMR 結果。

分譜的每一行（system）左邊通常印有該行第一小節的號碼。把它 OCR 出來，
就知道每一行「應該」有幾小節（下一行號碼 − 這一行號碼），再和 homr 認出的小節數比對：

- 少了 N 小節：多半是多小節休止（■12）被認成一小節 → 把該行的休止小節展開成 N+1 個整小節休止
- 多了 N 小節：多半是一小節被多認一條小節線切成兩半 → 把時值加起來剛好等於拍號的相鄰小節合併
- 無法判斷的行記在 alignment.json 裡，前端顯示給使用者手動處理

輸入：data/<piece>/pages/NNN.png 與 NNN.musicxml（homr 逐頁輸出）
輸出：pages/NNN.aligned.musicxml、score.musicxml（重新合併）、alignment.json
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from lxml import etree

import omr
from xmlsafe import PARSER

_ocr = None


def ocr_engine():
    global _ocr
    if _ocr is None:
        import logging

        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR

        _ocr = RapidOCR()
    return _ocr


# ---------- 影像：找五線譜與每行號碼 ----------

@dataclass
class Staff:
    lines: list[int]          # 5 條線的 y
    left: int                 # 譜線起點 x
    right: int

    @property
    def top(self) -> int:
        return self.lines[0]

    @property
    def bottom(self) -> int:
        return self.lines[-1]

    @property
    def space(self) -> float:
        return (self.lines[-1] - self.lines[0]) / 4


@dataclass
class System:
    staffs: list[Staff]
    number: int | None = None
    ocr: str = ""

    @property
    def top(self) -> int:
        return self.staffs[0].top

    @property
    def bottom(self) -> int:
        return self.staffs[-1].bottom


_page_cache: dict[str, tuple] = {}


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """掃描常有 0.x° 的歪斜，會讓譜線在頁面兩端差好幾個像素；用列投影的銳利度找最佳角度轉正。"""
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    best = (None, 0.0)
    for deg10 in range(-20, 21):
        ang = deg10 / 10
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        rot = cv2.warpAffine(small, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
        prof = (rot < 128).mean(axis=1)
        score = float(np.var(prof))
        if best[0] is None or score > best[0]:
            best = (score, ang)
    ang = best[1]
    if abs(ang) < 0.05:
        return gray, 0.0
    H, W = gray.shape
    M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
    return cv2.warpAffine(gray, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=255), ang


def load_page(png: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """回傳轉正後的 (bgr, gray, ink)；同一頁只處理一次。"""
    key = str(png)
    if key not in _page_cache:
        img = cv2.imread(key)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray, ang = deskew(gray)
        if ang:
            H, W = gray.shape
            M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
            img = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
        _page_cache.clear()
        _page_cache[key] = (img, gray, gray < 128)
    return _page_cache[key]


def detect_staffs(gray: np.ndarray) -> list[Staff]:
    h, w = gray.shape
    ink = gray < 128
    x0, x1 = int(w * 0.15), int(w * 0.85)
    row_ratio = ink[:, x0:x1].mean(axis=1)
    # 譜線 = 列黑度的局部峰值（用門檻切段會被密集音符黏成一大塊）
    lines: list[int] = []
    for y in range(4, h - 4):
        v = row_ratio[y]
        if v > 0.3 and v >= row_ratio[y - 4:y + 5].max() and (not lines or y - lines[-1] > 3):
            lines.append(y)
    # 常見的線距 s：取相鄰線距的眾數（4–40px）
    gaps = [b - a for a, b in zip(lines, lines[1:]) if 4 <= b - a <= 40]
    if not gaps:
        return []
    hist = np.bincount(gaps)
    s_mode = int(hist.argmax())
    tol = max(2, int(round(0.2 * s_mode)))
    # 從每條線出發，找 y+s, y+2s, y+3s, y+4s 附近的線（允許中間夾雜多餘的線）
    staffs: list[Staff] = []
    used: set[int] = set()
    arr = np.array(lines)
    for y in lines:
        if y in used:
            continue
        group = [y]
        ok = True
        for k in range(1, 5):
            target = y + k * s_mode
            cand = arr[(np.abs(arr - target) <= tol)]
            cand = [int(c) for c in cand if int(c) not in used and int(c) > group[-1]]
            if not cand:
                ok = False
                break
            group.append(min(cand, key=lambda c: abs(c - target)))
        if not ok:
            continue
        # 譜表左右端：5 條線裡至少 3 條有墨的欄位 → 膨脹填掉小縫 → 取最長的連續段
        rows_ink = np.stack([ink[max(0, ly - 4):ly + 5, :].any(axis=0) for ly in group])  # 譜線微彎，放寬 ±4px
        dense = rows_ink.sum(axis=0) >= 3
        kernel = 31
        dil = np.convolve(dense.astype(np.int32), np.ones(kernel, dtype=np.int32), mode="same") > 0
        xs = np.where(dil)[0]
        if not xs.size:
            continue
        runs = np.split(xs, np.where(np.diff(xs) > 1)[0] + 1)
        longest = max(runs, key=len)
        left, right = int(longest[0]) + kernel // 2, int(longest[-1]) - kernel // 2
        if right - left < 100:
            continue
        used.update(group)
        staffs.append(Staff(group, left, right))
    staffs.sort(key=lambda st: st.top)
    return staffs


def _joined(ink: np.ndarray, a: Staff, b: Staff) -> bool:
    """兩個譜表之間左端有貫穿的直線（系統小節線／大括號）→ 同一行（divisi）。"""
    gap = b.top - a.bottom
    if gap <= 0 or gap > 6 * (a.bottom - a.top):
        return False
    x = min(a.left, b.left)
    col = ink[a.bottom + 2:b.top - 2, max(0, x - 6):x + 10]
    if col.size == 0:
        return False
    return float(col.mean(axis=0).max()) > 0.85


def group_systems(staffs: list[Staff], ink: np.ndarray | None = None) -> list[System]:
    systems: list[System] = []
    for st in staffs:
        if systems and ink is not None and _joined(ink, systems[-1].staffs[-1], st):
            systems[-1].staffs.append(st)
            continue
        systems.append(System([st]))
    return systems


def read_system_numbers(img: np.ndarray, systems: list[System]) -> None:
    h, w = img.shape[:2]
    for sy in systems:
        st = sy.staffs[0]
        sp = st.space
        y0 = max(0, int(sy.top - 1.5 * sp * 4))
        y1 = min(h, int(sy.bottom + 1.5 * sp * 4))
        x1 = max(10, min(st.left for st in sy.staffs) - 4)
        crop = img[y0:y1, 0:x1]
        if crop.size == 0:
            continue
        center = (sy.top + sy.bottom) / 2 - y0
        best = None
        for scale, binarize in ((2, False), (3, True)):
            c = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if binarize:
                g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
                _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                c = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            try:
                res = ocr_engine()(c)
            except Exception:  # noqa: BLE001
                continue
            if not res.txts:
                continue
            for txt, score, box in zip(res.txts, res.scores, res.boxes):
                m = re.fullmatch(r"(\d{1,4})\.?", txt.strip())
                if not m or score < 0.5:
                    continue
                by = float(np.mean([p[1] for p in box])) / scale
                dist = abs(by - center)
                if best is None or dist < best[0]:
                    best = (dist, int(m.group(1)), txt)
            if best:
                break
        if best and best[0] < 1.3 * (sy.bottom - sy.top) + 2 * sp:  # 號碼在譜表垂直中央附近，太遠的是鄰行的
            sy.number, sy.ocr = best[1], best[2]


def local_lines(ink: np.ndarray, st: Staff, x0: int, x1: int) -> list[int]:
    """譜線在頁面上略有彎曲／傾斜：在 x0–x1 這一段重新找 5 條線的 y。"""
    base = max(0, st.top - 10)
    prof = ink[base:st.bottom + 11, x0:x1].mean(axis=1)
    ys = []
    for ly in st.lines:
        c = ly - base
        lo, hi = max(0, c - 7), min(len(prof), c + 8)
        ys.append(base + lo + int(np.argmax(prof[lo:hi])) if hi > lo else ly)
    return ys


def digits_above_staff(img: np.ndarray, st: Staff) -> list[tuple[float, int]]:
    """讀出譜表上方的數字 → [(x 相對位置 0–1, 數字)]。整條太寬 OCR 會漏，改用重疊的視窗掃。"""
    sp = st.space
    y0, y1 = max(0, int(st.top - 5 * sp)), max(0, int(st.top - 0.3 * sp))
    if y1 - y0 < 8:
        return []
    width = max(1, st.right - st.left)
    win, step = 420, 300
    found: list[tuple[float, int, float]] = []
    for wx in range(0, width, step):
        x0, x1 = st.left + wx, min(st.right, st.left + wx + win)
        if x1 - x0 < 40:
            continue
        for scale, binarize in ((2, False), (3, True)):
            crop = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if binarize:
                g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                crop = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            try:
                res = ocr_engine()(crop)
            except Exception:  # noqa: BLE001
                continue
            if res.txts is None or res.boxes is None:
                continue
            got = False
            for txt, score, box in zip(res.txts, res.scores, res.boxes):
                m = re.fullmatch(r"(\d{1,3})", txt.strip())
                if not m or score < 0.5:
                    continue
                xc = (x0 - st.left + float(np.mean([p[0] for p in box])) / scale) / width
                found.append((xc, int(m.group(1)), float(score)))
                got = True
            if got:
                break
    found.sort()
    out: list[tuple[float, int]] = []
    for xc, v, sc in found:  # 視窗重疊會重複讀到 → 位置很近的只留一個
        if out and abs(out[-1][0] - xc) < 25 / width:
            continue
        out.append((xc, v))
    return out


def staff_area_empty(ink: np.ndarray, st: Staff, x_frac: float) -> bool:
    """數字正下方：第 1、4 空間和譜表下方幾乎沒有墨 → 只有多小節休止符號（它們只佔中間的空間），
    不是連音的 3、指法或音符。"""
    sp = st.space
    width = st.right - st.left
    xc = st.left + int(x_frac * width)
    x0, x1 = max(st.left, xc - int(2.5 * sp)), min(st.right, xc + int(2.5 * sp))
    if x1 - x0 < 4:
        return False
    ls = local_lines(ink, st, x0, x1)
    parts = [ink[ls[0] + 3:ls[1] - 2, x0:x1], ink[ls[3] + 3:ls[4] - 2, x0:x1], ink[ls[4] + 4:ls[4] + int(1.5 * sp), x0:x1]]
    return all(p.size == 0 or float(p.mean()) < 0.02 for p in parts)


def multirests_in_row(png: Path, sy: System) -> list[tuple[float, int]]:
    """行裡的多小節休止：[(x 相對位置, 小節數)]。"""
    img, _gray, ink = load_page(png)
    st = sy.staffs[0]
    return [(x, v) for x, v in digits_above_staff(img, st) if 1 <= v <= 200 and staff_area_empty(ink, st, x)]


def analyze_image(path: Path) -> list[System]:
    img, gray, ink = load_page(path)
    systems = group_systems(detect_staffs(gray), ink)
    read_system_numbers(img, systems)
    return systems


# ---------- XML：每行的小節 ----------

def xml_systems(root) -> list[list]:
    """依 <print new-system="yes"/> 把 part 的小節切成行。"""
    part = root.find("part")
    rows: list[list] = [[]]
    for m in part.findall("measure"):
        pr = m.find("print")
        if pr is not None and pr.get("new-system") == "yes" and rows[-1]:
            rows.append([])
        rows[-1].append(m)
    return rows


def _fill(measure) -> int:
    pos = last = mx = 0
    for el in measure:
        if el.tag == "backup":
            pos -= int(el.findtext("duration") or 0)
        elif el.tag == "forward":
            pos += int(el.findtext("duration") or 0)
            mx = max(mx, pos)
        elif el.tag == "note":
            if el.find("chord") is not None:
                continue
            last = pos
            if el.find("grace") is None:
                pos += int(el.findtext("duration") or 0)
            mx = max(mx, pos)
    return mx


def is_rest_only(measure) -> bool:
    notes = measure.findall("note")
    return all(n.find("rest") is not None for n in notes) and measure.find("backup") is None


def multirest_value(measure) -> int | None:
    v = measure.findtext("attributes/measure-style/multiple-rest")
    return int(v) if v and v.isdigit() else None


def make_rest_measure(nominal: int):
    m = etree.Element("measure", number="0")
    n = etree.SubElement(m, "note")
    etree.SubElement(n, "rest", measure="yes")
    etree.SubElement(n, "duration").text = str(max(1, nominal))
    return m


def expand_rest(measure, extra: int, nominal: int) -> None:
    """把休止小節展開成 extra+1 個整小節休止。"""
    for ms in measure.findall("attributes/measure-style"):
        ms.getparent().remove(ms)
    notes = measure.findall("note")
    for n in notes[1:]:
        measure.remove(n)
    if not notes:
        n = etree.SubElement(measure, "note")
        etree.SubElement(n, "rest", measure="yes")
        etree.SubElement(n, "duration").text = str(nominal)
        notes = [n]
    if notes:
        rest, dur = notes[0].find("rest"), notes[0].find("duration")
        rest.set("measure", "yes")
        if dur is not None:
            dur.text = str(nominal)
        t = notes[0].find("type")
        if t is not None:
            notes[0].remove(t)
    parent, anchor = measure.getparent(), measure
    for _ in range(extra):
        m = make_rest_measure(nominal)
        parent.insert(parent.index(anchor) + 1, m)
        anchor = m


def merge_into(a, b) -> None:
    """把 b 的內容接到 a 後面（a、b 相鄰，時值加起來剛好一小節）。"""
    for el in list(b):
        if el.tag in ("attributes", "print"):
            continue
        a.append(el)
    b.getparent().remove(b)


# ---------- 對齊主流程 ----------

@dataclass
class Row:
    page: int
    index: int
    number: int | None
    ocr: str
    measures: list = field(default_factory=list)
    staffs: int = 1
    expected: int | None = None
    action: str = ""
    resolved: bool = True
    unreliable: bool = False
    system: System | None = None
    png: Path | None = None


def collect_rows(pdir: Path) -> tuple[list[Row], dict[int, etree._ElementTree], list[str]]:
    rows: list[Row] = []
    trees: dict[int, etree._ElementTree] = {}
    notes: list[str] = []
    pages = sorted((pdir / "pages").glob("[0-9][0-9][0-9].png"))
    for png in pages:
        page_no = int(png.stem)
        xml = png.with_suffix(".musicxml")
        if not xml.exists():
            continue
        tree = etree.parse(str(xml), PARSER)
        trees[page_no] = tree
        xrows = xml_systems(tree.getroot())
        systems = analyze_image(png)
        # divisi（一行兩個譜表）：homr 可能把第二個譜表也當成一行 → 丟掉那些重複的小節
        if len(systems) < len(xrows):
            merged: list[list] = []
            k = 0
            for sy in systems:
                if k >= len(xrows):
                    break
                merged.append(xrows[k])
                k += 1
                for _ in range(len(sy.staffs) - 1):
                    if k < len(xrows) and len(systems) - len(merged) < len(xrows) - k:
                        dup = xrows[k]
                        for m in dup:
                            m.getparent().remove(m)
                        k += 1
            while k < len(xrows):  # 還有多的行：接在最後
                merged.append(xrows[k])
                k += 1
            xrows = merged
        bad_page = len(systems) != len(xrows)
        if bad_page:
            notes.append(f"第 {page_no} 頁：影像找到 {len(systems)} 行，OMR 有 {len(xrows)} 行，這一頁不自動對齊")
        n = max(len(systems), len(xrows))
        for i in range(n):
            sy = systems[i] if i < len(systems) else None
            ms = xrows[i] if i < len(xrows) else []
            r = Row(page_no, i, sy.number if sy else None, sy.ocr if sy else "", ms, len(sy.staffs) if sy else 1, system=sy, png=png)
            if bad_page:
                r.unreliable, r.number, r.ocr, r.action, r.resolved = True, None, "(行數對不上)", "影像與 OMR 的行數對不上，略過", False
            rows.append(r)
    return rows, trees, notes


def _consistent(a: Row, b: Row, ai: int, bi: int) -> bool:
    rows_between = bi - ai
    diff = b.number - a.number
    return rows_between >= 1 and rows_between <= diff <= 16 * rows_between


def _reset(a: Row, b: Row, c: Row | None, bi: int, ci: int | None) -> bool:
    """樂章交界：號碼從頭數（b 小），而且 b 之後的號碼要接得上。"""
    return b.number < a.number and b.number <= 40 and (c is None or _consistent(b, c, bi, ci))


def sanitize_numbers(rows: list[Row]) -> None:
    """去掉 OCR 讀錯的號碼：和前後都接不上的號碼視為沒讀到。允許樂章交界重新計數。"""
    if rows:  # 第一行是第 1 小節（這種版本第一行不印號碼）
        rows[0].number, rows[0].ocr = 1, "(assumed)"
    changed = True
    while changed:
        changed = False
        idx = [i for i, r in enumerate(rows) if r.number is not None]
        for k, i in enumerate(idx):
            cur = rows[i]
            prev_i = idx[k - 1] if k > 0 else None
            next_i = idx[k + 1] if k + 1 < len(idx) else None
            next2_i = idx[k + 2] if k + 2 < len(idx) else None
            ok_prev = prev_i is None or _consistent(rows[prev_i], cur, prev_i, i) or _reset(rows[prev_i], cur, rows[next_i] if next_i is not None else None, i, next_i)
            ok_next = next_i is None or _consistent(cur, rows[next_i], i, next_i) or _reset(cur, rows[next_i], rows[next2_i] if next2_i is not None else None, next_i, next2_i)
            if not ok_prev and not ok_next:
                cur.ocr, cur.number = f"ignored:{cur.ocr}", None
                changed = True
                break
        if not changed:
            # 還有接不上的邊：兩端各自另一邊都好 → 拿掉「跟後面接不上」的那個（較保守）
            for k in range(len(idx) - 1):
                a, b = rows[idx[k]], rows[idx[k + 1]]
                c = rows[idx[k + 2]] if k + 2 < len(idx) else None
                if not _consistent(a, b, idx[k], idx[k + 1]) and not _reset(a, b, c, idx[k + 1], idx[k + 2] if k + 2 < len(idx) else None):
                    prev_a = rows[idx[k - 1]] if k > 0 else None
                    a_bad = prev_a is not None and not _consistent(prev_a, a, idx[k - 1], idx[k]) and not _reset(prev_a, a, b, idx[k], idx[k + 1])
                    b_bad = c is not None and not _consistent(b, c, idx[k + 1], idx[k + 2]) and not _reset(b, c, None, idx[k + 2], None)
                    victim = a if (a_bad and not b_bad and a.ocr != "(assumed)") else b
                    victim.ocr, victim.number = f"ignored:{victim.ocr}", None
                    changed = True
                    break


def nominal_of(row_measures, trees_state) -> int:
    return trees_state["nominal"]


def _pick_digits(digits: list[int], n: int, delta: int) -> list[int] | None:
    """從譜上讀到的數字裡挑 n 個（保持順序），使 Σ(d−1) = delta；唯一解才採用。"""
    from itertools import combinations

    if len(digits) < n:
        return None
    sols = [c for c in combinations(digits, n) if sum(d - 1 for d in c) == delta]
    uniq = {tuple(c) for c in sols}
    return list(uniq.pop()) if len(uniq) == 1 else None


def align_rows(rows: list[Row]) -> None:
    # 每個小節的拍號長度：依序掃描所有小節維持狀態
    state = {"div": 1, "beats": 4, "beat_type": 4}
    nominal: dict[int, int] = {}
    for r in rows:
        for m in r.measures:
            nominal[id(m)] = omr._measure_nominal(m, state)

    numbered = [i for i, r in enumerate(rows) if r.number is not None]
    for a, b in zip(numbered, numbered[1:]):
        span = rows[a:b]
        expected = rows[b].number - rows[a].number
        if expected <= 0:  # 樂章交界（號碼重新計數）：這一段不知道該有幾小節
            for r in span:
                r.action = "樂章交界，無法核對"
            continue
        got = sum(len(r.measures) for r in span)
        delta = expected - got
        for r in span:
            r.expected = expected if len(span) == 1 else None
        if delta == 0:
            continue
        target = span[0] if len(span) == 1 else None
        label = f"第 {rows[a].page} 頁第 {rows[a].index + 1} 行" + ("" if len(span) == 1 else f"（到第 {rows[b].page} 頁第 {rows[b].index + 1} 行前）")
        measures = [m for r in span for m in r.measures]
        if delta > 0:
            cands = [m for m in measures if is_rest_only(m)]
            multi = [m for m in cands if multirest_value(m)]
            blocks: list[tuple[float, int]] = []
            if len(span) == 1 and span[0].system is not None and span[0].png is not None:
                blocks = multirests_in_row(span[0].png, span[0].system)
            if blocks and sum(v - 1 for _, v in blocks) == delta:
                # 依粗棒的水平位置估計是第幾小節：±1 內有休止小節就展開它，否則在那之後插入
                n_m = len(measures)
                sure = True
                for xc, v in blocks:
                    if v <= 1:
                        continue
                    est = min(n_m - 1, max(0, int(xc * n_m)))
                    pick = next((measures[j] for j in (est, est - 1, est + 1) if 0 <= j < n_m and is_rest_only(measures[j])), None)
                    if pick is not None:
                        expand_rest(pick, v - 1, nominal[id(pick)])
                    else:
                        sure = False
                        anchor = measures[est]
                        parent = anchor.getparent()
                        nom = nominal[id(anchor)]
                        for _ in range(v - 1):
                            nm = make_rest_measure(nom)
                            parent.insert(parent.index(anchor) + 1, nm)
                            anchor = nm
                action = f"少 {delta} 小節 → 依譜上多小節休止 {'+'.join(str(v) for _, v in blocks)} 展開"
                if not sure:
                    action += "（OMR 把休止棒認成音符，已在該處插入空小節，請確認）"
            elif len(cands) == 1:
                expand_rest(cands[0], delta, nominal[id(cands[0])])
                action = f"少 {delta} 小節 → 休止小節展開"
            elif multi and sum((multirest_value(m) or 1) - 1 for m in multi) == delta:
                for m in multi:
                    expand_rest(m, (multirest_value(m) or 1) - 1, nominal[id(m)])
                action = f"少 {delta} 小節 → 依 OMR 的多小節休止數展開"
            elif cands:
                expand_rest(cands[0], delta, nominal[id(cands[0])])
                action = f"少 {delta} 小節 → 展開第一個休止小節（有 {len(cands)} 個候選，譜上休止棒 {blocks}，請確認）"
                for r in span:
                    r.resolved = False
            else:
                action = f"少 {delta} 小節，找不到休止小節可展開（譜上休止棒 {blocks}）"
                for r in span:
                    r.resolved = False
        else:
            need = -delta
            merged = 0
            i = 0
            while i + 1 < len(measures) and merged < need:
                m1, m2 = measures[i], measures[i + 1]
                nom = nominal[id(m1)]
                if _fill(m1) < nom and _fill(m2) < nom and _fill(m1) + _fill(m2) == nom and m1.getparent() is m2.getparent():
                    merge_into(m1, m2)
                    for r in span:
                        if m2 in r.measures:
                            r.measures.remove(m2)
                    measures.pop(i + 1)
                    merged += 1
                else:
                    i += 1
            action = f"多 {need} 小節 → 合併了 {merged} 對被切開的小節"
            if merged < need:
                fills = [f"{_fill(m)}/{nominal[id(m)]}" for m in measures]
                action += f"；各小節時值 {' '.join(fills)}"
                for r in span:
                    r.resolved = False
                action += "，仍多 %d 小節" % (need - merged)
        for r in span:
            r.action = action
        if target is None:
            span[0].action = label + "：" + action


def align_piece(piece_id: str, apply: bool = True) -> dict:
    pdir = omr.piece_dir(piece_id)
    rows, trees, notes = collect_rows(pdir)
    sanitize_numbers(rows)
    align_rows(rows)
    report = {
        "pages": {},
        "notes": notes,
        "numbers_read": sum(1 for r in rows if r.number is not None),
        "rows": len(rows),
        "unresolved": [],
    }
    for r in rows:
        report["pages"].setdefault(r.page, []).append(
            {"row": r.index + 1, "number": r.number, "ocr": r.ocr, "measures": len(r.measures), "staffs": r.staffs, "action": r.action, "resolved": r.resolved}
        )
        if not r.resolved:
            report["unresolved"].append(f"第 {r.page} 頁第 {r.index + 1} 行（{r.number or '?'}）：{r.action}")
    if apply:
        files = []
        for page_no, tree in sorted(trees.items()):
            out = pdir / "pages" / f"{page_no:03d}.aligned.musicxml"
            tree.write(str(out), xml_declaration=True, encoding="UTF-8", pretty_print=True)
            files.append(out)
        if files:
            import works

            works.write_piece_score(pdir, omr.merge_musicxml(files))
            root = etree.parse(str(pdir / "score.musicxml"), PARSER).getroot()
            report["total_measures"] = len(root.find("part").findall("measure"))
        (pdir / "alignment.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：uv run python align.py <piece_id> [--dry]")
        sys.exit(1)
    rep = align_piece(sys.argv[1], apply="--dry" not in sys.argv)
    for page, rows in rep["pages"].items():
        for r in rows:
            print(f"p{page} 行{r['row']:2d} 號碼={str(r['number']):>5} ({r['ocr']}) OMR小節={r['measures']:2d} 譜表={r['staffs']} {r['action']}")
    print("讀到號碼", rep["numbers_read"], "/", rep["rows"], "行；未解決", len(rep["unresolved"]), "；總小節", rep.get("total_measures"))
    for n in rep["notes"]:
        print("!", n)
