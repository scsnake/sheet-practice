"""把管樂／銅管（或任何一組樂器）的分部檔縮成一份「鋼琴式」兩行譜表的伴奏聲部。

給弦樂練習用：弦樂手要的是和聲背景，不是完整的管樂總譜。做法是把每個小節切成
「有音符開始或結束」的片段，每個片段把當下發聲的音疊成一個和弦寫進高音／低音譜表，
跨片段的音用連結線（tie）接起來。這樣每小節的時值一定剛好等於拍號，播放的時間軸不會亂。

用法：
  uv run python reduce_orchestra.py <作品資料夾>                                   # 讀 Winds/ 與 Brass/，寫到 Reduction/
  uv run python reduce_orchestra.py <作品資料夾> --from Winds Brass --out <資料夾> --max-notes 6
  uv run python reduce_orchestra.py <作品資料夾> --check                          # 只檢查來源檔有沒有超出小節長度的小節

輸出每個樂章一個檔案，接著就能用 import_parts.py 匯入：
  uv run python import_parts.py "<作品名稱>" Reduction/*_Reduction.musicxml --part ".*=Winds & Brass"
"""

from __future__ import annotations

import argparse
import math
import re
from fractions import Fraction
from pathlib import Path

from lxml import etree

from xmlsafe import PARSER

SPLIT_MIDI = 60          # 中央 C：以上放高音譜表，以下放低音譜表
TYPES = [(4, "whole"), (2, "half"), (1, "quarter"), (Fraction(1, 2), "eighth"),
         (Fraction(1, 4), "16th"), (Fraction(1, 8), "32nd"), (Fraction(1, 16), "64th")]
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0), ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
FLAT = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0), ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]


def load(path: Path):
    return etree.parse(str(path), PARSER).getroot()


def part_of(root):
    p = root.find("part")
    return p


def transpose_of(part) -> int:
    """該聲部記譜音高 → 實際音高要加的半音數（單簧管、法國號…）。"""
    tr = part.find("measure/attributes/transpose")
    if tr is None:
        return 0
    return int(tr.findtext("chromatic") or 0) + 12 * int(tr.findtext("octave-change") or 0)


def midi_of(note) -> int | None:
    p = note.find("pitch")
    if p is None:
        return None
    return (int(p.findtext("octave")) + 1) * 12 + STEPS[p.findtext("step")] + int(p.findtext("alter") or 0)


def measure_plan(part) -> list[dict]:
    """逐小節讀出 divisions、拍號應有長度、調號。"""
    div, beats, bt, fifths = 1, 4, 4, 0
    out = []
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            d = a.findtext("divisions")
            if d:
                div = int(d)
            t = a.find("time")
            if t is not None:
                beats, bt = int(t.findtext("beats")), int(t.findtext("beat-type"))
            k = a.findtext("key/fifths")
            if k is not None:
                fifths = int(k)
        out.append({"div": div, "beats": beats, "bt": bt, "fifths": fifths,
                    "len": int(round(div * 4 * beats / bt))})
    return out


def exact_divisions(plans: list[list[dict]]) -> int:
    """所有來源都能整除的 divisions：連音（例如 6 對 4）換算後位置才不會被捨去，每小節的長度也一定是整數。"""
    d = 1
    for plan in plans:
        for pm in plan:
            d = math.lcm(d, pm["div"], Fraction(4 * pm["beats"], pm["bt"]).denominator)
    return d


def fifths_shift(semitones: int) -> int:
    """移調幾個半音 → 調號要加幾個升降記號（-2 → -2，-7 → -1）。"""
    return (semitones * 7 + 5) % 12 - 5


def concert_keys(candidates: list[tuple], ref_part, ref_semis: int) -> list[int]:
    """逐小節的實際音高調號。音高都換成實際音高了，調號也要一樣。

    優先用不移調的聲部（弦樂基準）；法國號常常不寫調號，所以寫譜調號換算只當最後手段。
    """
    for part, semis in candidates:
        if semis % 12 == 0:
            return [pm["fifths"] for pm in measure_plan(part)]
    keys = [pm["fifths"] + fifths_shift(ref_semis) for pm in measure_plan(ref_part)]
    return [k - 12 if k > 7 else k + 12 if k < -7 else k for k in keys]


def measure_fill(m) -> int:
    """這一小節實際被填到多長（divisions）：不完整小節（樂章中的不完整小節、弱起）要照原樣保留。"""
    pos = last = mx = 0
    for e in m:
        if e.tag == "backup":
            pos -= int(e.findtext("duration") or 0)
        elif e.tag == "forward":
            pos += int(e.findtext("duration") or 0)
            mx = max(mx, pos)
        elif e.tag == "note":
            if e.find("chord") is not None:
                continue
            last = pos
            if e.find("grace") is None:
                pos += int(e.findtext("duration") or 0)
            mx = max(mx, pos)
    return mx


def events(part, semitones: int, plan: list[dict]) -> list[list[tuple[int, int, int]]]:
    """每小節的 (起點, 長度, midi)；已換成實際音高，超出小節長度的音會被截掉。"""
    per_measure = []
    for mi, m in enumerate(part.findall("measure")):
        nominal = plan[min(mi, len(plan) - 1)]["len"]
        pos = last = 0
        evs = []
        for e in m:
            if e.tag == "backup":
                pos -= int(e.findtext("duration") or 0)
            elif e.tag == "forward":
                pos += int(e.findtext("duration") or 0)
            elif e.tag == "note":
                dur = int(e.findtext("duration") or 0)
                chord = e.find("chord") is not None
                grace = e.find("grace") is not None
                start = last if chord else pos
                if not chord:
                    last = pos
                    if not grace:
                        pos += dur
                if grace or dur <= 0:
                    continue
                midi = midi_of(e)
                if midi is None:          # 休止符、無音高打擊樂
                    continue
                end = min(start + dur, nominal)
                if start >= nominal:      # 來源檔壞掉：音符整個在小節之外，丟掉
                    continue
                evs.append((start, end - start, midi + semitones))
        per_measure.append(evs)
    return per_measure


def spell(midi: int, flat: bool) -> tuple[str, int, int]:
    step, alter = (FLAT if flat else SHARP)[midi % 12]
    return step, alter, midi // 12 - 1


def note_type(dur: int, div: int) -> tuple[str | None, int]:
    """時值 → (type, 附點數)；不是標準時值就不寫 type（播放仍然正確）。"""
    q = Fraction(dur, div)
    for base, name in TYPES:
        if q == Fraction(base):
            return name, 0
        if q == Fraction(base) * 3 / 2:
            return name, 1
    return None, 0


def simplify(pitches: list[int], cap: int) -> list[int]:
    """和弦太密時：保留最低、最高，中間挑音高類別不重複的，最多 cap 個。"""
    ps = sorted(set(pitches))
    if len(ps) <= cap:
        return ps
    keep = [ps[0], ps[-1]]
    seen = {ps[0] % 12, ps[-1] % 12}
    for p in ps[1:-1]:
        if len(keep) >= cap:
            break
        if p % 12 not in seen:
            keep.append(p)
            seen.add(p % 12)
    for p in ps[1:-1]:
        if len(keep) >= cap:
            break
        if p not in keep:
            keep.append(p)
    return sorted(keep)


def build_measure(m_out, segs, staff: int, voice: int, plan: dict, cap: int, carry: set[int]) -> set[int]:
    """把一個譜表的片段寫進小節；回傳「延續到下一個片段」的音（用來接 tie）。"""
    div, fifths = plan["div"], plan["fifths"]
    flat = fifths < 0
    sounding_next: set[int] = set()
    for si, (start, end, pitches) in enumerate(segs):
        dur = end - start
        if dur <= 0:
            continue
        typ, dots = note_type(dur, div)
        keep = simplify(pitches, cap) if pitches else []
        nxt = segs[si + 1][2] if si + 1 < len(segs) else []
        if not keep:
            n = etree.SubElement(m_out, "note")
            etree.SubElement(n, "rest")
            etree.SubElement(n, "duration").text = str(dur)
            etree.SubElement(n, "voice").text = str(voice)
            if typ:
                etree.SubElement(n, "type").text = typ
            for _ in range(dots):
                etree.SubElement(n, "dot")
            etree.SubElement(n, "staff").text = str(staff)
            carry = set()
            continue
        for ci, midi in enumerate(keep):
            n = etree.SubElement(m_out, "note")
            if ci:
                etree.SubElement(n, "chord")
            step, alter, octave = spell(midi, flat)
            p = etree.SubElement(n, "pitch")
            etree.SubElement(p, "step").text = step
            if alter:
                etree.SubElement(p, "alter").text = str(alter)
            etree.SubElement(p, "octave").text = str(octave)
            etree.SubElement(n, "duration").text = str(dur)
            tie_stop, tie_start = midi in carry, midi in nxt
            if tie_stop:
                etree.SubElement(n, "tie", type="stop")
            if tie_start:
                etree.SubElement(n, "tie", type="start")
            etree.SubElement(n, "voice").text = str(voice)
            if typ:
                etree.SubElement(n, "type").text = typ
            for _ in range(dots):
                etree.SubElement(n, "dot")
            etree.SubElement(n, "staff").text = str(staff)
            if tie_stop or tie_start:
                nots = etree.SubElement(n, "notations")
                if tie_stop:
                    etree.SubElement(nots, "tied", type="stop")
                if tie_start:
                    etree.SubElement(nots, "tied", type="start")
        carry = set(keep) & set(nxt)
        sounding_next = carry
    return sounding_next


def segments(evs: list[tuple[int, int, int]], nominal: int) -> list[tuple[int, int, list[int]]]:
    """把小節切成「有音開始或結束」的片段，每段列出當下發聲的音。"""
    if not evs:
        return [(0, nominal, [])]
    marks = {0, nominal}
    for start, dur, _ in evs:
        marks.add(start)
        marks.add(min(start + dur, nominal))
    pts = sorted(p for p in marks if 0 <= p <= nominal)
    out = []
    for a, b in zip(pts, pts[1:]):
        sounding = [midi for start, dur, midi in evs if start <= a < start + dur]
        out.append((a, b, sounding))
    return out


def reduce_movement(files: list[Path], out_path: Path, name: str, cap: int, ref_files: list[Path] | None = None) -> dict:
    parts = []
    for f in files:
        root = load(f)
        p = part_of(root)
        if p is None:
            continue
        parts.append((f, p, transpose_of(p)))
    if not parts:
        raise SystemExit(f"{out_path.name}: 找不到任何聲部")
    ref_part, ref_semis = max(parts, key=lambda x: len(x[1].findall("measure")))[1:]
    refs = [rp for rp in (part_of(load(rf)) for rf in ref_files or []) if rp is not None]
    plan = measure_plan(ref_part)
    nbars = len(plan)
    all_events: list[list[tuple[int, int, int]]] = [[] for _ in range(nbars)]
    div0 = exact_divisions([plan, *(measure_plan(p) for _, p, _ in parts), *(measure_plan(rp) for rp in refs)])
    keys = concert_keys([(rp, transpose_of(rp)) for rp in refs] + [(p, semis) for _, p, semis in parts], ref_part, ref_semis)
    for mi, pm in enumerate(plan):
        pm["div"] = div0
        pm["len"] = int(Fraction(div0 * 4 * pm["beats"], pm["bt"]))
        pm["fifths"] = keys[min(mi, len(keys) - 1)]
    fills = [0] * nbars
    for f, p, semis in parts:
        pplan = measure_plan(p)
        evs = events(p, semis, pplan)
        pms = p.findall("measure")
        for mi in range(min(nbars, len(pms))):
            scale = Fraction(div0, pplan[min(mi, len(pplan) - 1)]["div"])
            fills[mi] = max(fills[mi], int(measure_fill(pms[mi]) * scale))
            for start, dur, midi in evs[mi]:
                all_events[mi].append((int(start * scale), int(dur * scale), midi))

    for rp in refs:   # 以弦樂分部當基準：同一小節的長度要一樣，管樂自己短（不完整小節）才不會被當成錯誤
        rplan = measure_plan(rp)
        for mi, m in enumerate(rp.findall("measure")[:nbars]):
            q = Fraction(measure_fill(m), rplan[min(mi, len(rplan) - 1)]["div"])
            fills[mi] = max(fills[mi], int(q * div0))

    out = etree.Element("score-partwise", version="4.0")
    pl = etree.SubElement(out, "part-list")
    sp = etree.SubElement(pl, "score-part", id="P1")
    etree.SubElement(sp, "part-name").text = name
    part_out = etree.SubElement(out, "part", id="P1")
    kept = dropped = short = 0
    carry_hi: set[int] = set()
    carry_lo: set[int] = set()
    for mi in range(nbars):
        pm = plan[mi]
        m = etree.SubElement(part_out, "measure", number=str(mi + 1))
        if mi == 0 or plan[mi]["div"] != plan[mi - 1]["div"] or plan[mi]["beats"] != plan[mi - 1]["beats"] \
                or plan[mi]["bt"] != plan[mi - 1]["bt"] or plan[mi]["fifths"] != plan[mi - 1]["fifths"]:
            a = etree.SubElement(m, "attributes")
            etree.SubElement(a, "divisions").text = str(pm["div"])
            k = etree.SubElement(a, "key")
            etree.SubElement(k, "fifths").text = str(pm["fifths"])
            t = etree.SubElement(a, "time")
            etree.SubElement(t, "beats").text = str(pm["beats"])
            etree.SubElement(t, "beat-type").text = str(pm["bt"])
            etree.SubElement(a, "staves").text = "2"
            for num, sign, line in ((1, "G", 2), (2, "F", 4)):
                c = etree.SubElement(a, "clef", number=str(num))
                etree.SubElement(c, "sign").text = sign
                etree.SubElement(c, "line").text = str(line)
        bar_len = min(fills[mi] or pm["len"], pm["len"])   # 不完整小節照來源的長度，壞掉的（超出拍號）則截到拍號長度
        short += bar_len < pm["len"]
        hi = [e for e in all_events[mi] if e[2] >= SPLIT_MIDI and e[0] < bar_len]
        lo = [e for e in all_events[mi] if e[2] < SPLIT_MIDI and e[0] < bar_len]
        kept += len(hi) + len(lo)
        segs_hi, segs_lo = segments(hi, bar_len), segments(lo, bar_len)
        dropped += sum(max(0, len(set(s[2])) - cap) for s in segs_hi + segs_lo)
        carry_hi = build_measure(m, segs_hi, 1, 1, pm, cap, carry_hi)
        b = etree.SubElement(m, "backup")
        etree.SubElement(b, "duration").text = str(bar_len)
        carry_lo = build_measure(m, segs_lo, 2, 5, pm, cap, carry_lo)
    out_path.write_bytes(etree.tostring(out, xml_declaration=True, encoding="UTF-8", pretty_print=True))
    return {"bars": nbars, "sources": len(parts), "notes_in": kept, "chord_notes_trimmed": dropped, "short_bars": short}


def check_sources(files: list[Path]) -> tuple[int, int]:
    """回傳 (超出小節長度的小節數, 其中含真正音符的)。"""
    over = overnote = 0
    for f in files:
        root = load(f)
        for p in root.findall("part"):
            plan = measure_plan(p)
            for mi, m in enumerate(p.findall("measure")):
                nominal = plan[min(mi, len(plan) - 1)]["len"]
                pos = last = 0
                bad = badnote = False
                for e in m:
                    if e.tag == "backup":
                        pos -= int(e.findtext("duration") or 0)
                    elif e.tag == "forward":
                        pos += int(e.findtext("duration") or 0)
                    elif e.tag == "note":
                        dur = int(e.findtext("duration") or 0)
                        chord = e.find("chord") is not None
                        start = last if chord else pos
                        if not chord:
                            last = pos
                            if e.find("grace") is None:
                                pos += dur
                        if start >= nominal or (e.find("grace") is None and start + dur > nominal):
                            bad = True
                            if e.find("rest") is None:
                                badnote = True
                over += bad
                overnote += badnote
    return over, overnote


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work_dir", type=Path, help="底下有 Winds/、Brass/、Strings/ 的作品資料夾")
    ap.add_argument("--from", dest="groups", nargs="+", default=["Winds", "Brass"], help="要縮譜的子資料夾")
    ap.add_argument("--out", type=Path, default=None, help="輸出資料夾（預設 <作品資料夾>/Reduction）")
    ap.add_argument("--name", default="Winds & Brass", help="聲部名稱")
    ap.add_argument("--max-notes", type=int, default=6, help="一個和弦最多幾個音（太密才會簡化）")
    ap.add_argument("--reference", type=Path, default=None,
                    help="用來對齊小節長度的分部資料夾（預設 <作品資料夾>/Strings）；不完整小節照它的長度")
    ap.add_argument("--check", action="store_true", help="只檢查來源檔的小節時值")
    args = ap.parse_args()

    dirs = [args.work_dir / g for g in args.groups]
    files = sorted(f for d in dirs if d.exists() for f in d.glob("*.musicxml"))
    if not files:
        raise SystemExit(f"{[str(d) for d in dirs]} 裡沒有 .musicxml")
    mvts: dict[int, list[Path]] = {}
    for f in files:
        m = re.search(r"mvt(\d+)", f.name)
        mvts.setdefault(int(m.group(1)) if m else 1, []).append(f)

    if args.check:
        for mvt, fs in sorted(mvts.items()):
            over, overnote = check_sources(fs)
            print(f"mvt{mvt}: {len(fs)} 個來源檔，超出小節長度的小節 {over}（含真正音符 {overnote}）")
        return

    ref_dir = args.reference if args.reference is not None else (args.work_dir / "Strings")
    refs: dict[int, list[Path]] = {}
    if ref_dir and ref_dir.exists():
        for f in sorted(ref_dir.glob("*.musicxml")):
            m = re.search(r"mvt(\d+)", f.name)
            refs.setdefault(int(m.group(1)) if m else 1, []).append(f)

    out_dir = args.out or (args.work_dir / "Reduction")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.work_dir.name
    for mvt, fs in sorted(mvts.items()):
        over, overnote = check_sources(fs)
        out = out_dir / f"{stem}_mvt{mvt}_{args.name.replace(' & ', '_').replace(' ', '_')}_Reduction.musicxml"
        info = reduce_movement(fs, out, args.name, args.max_notes, refs.get(mvt))
        print(f"mvt{mvt}: {info['sources']} 個分部 → {out.name}；{info['bars']} 小節、{info['notes_in']} 個音"
              f"（和弦超過 {args.max_notes} 音而省略 {info['chord_notes_trimmed']} 個；不完整小節 {info['short_bars']} 個照原長度保留）"
              f"；來源超出小節長度的小節 {over}（含音符 {overnote}）")


if __name__ == "__main__":
    main()
