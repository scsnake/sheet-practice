"""檢查並整理「已處理好」的作品資料，準備給 app 直接使用。

匯入之後先跑這個檢查，譜的品質要在這一步就顧好：
壞掉的小節會讓播放時間軸整段錯開，各聲部對不齊，在畫面上不容易看出原因。

  uv run python prepare.py                       # 檢查所有作品
  uv run python prepare.py --work "作品名稱"     # 只檢查一個作品
  uv run python prepare.py --fix                 # 順便修掉可以自動修的問題（會先備份）

檢查項目
  小節數     各聲部的小節數要一樣（合併時短的會補空小節，等於整段錯位）
  小節時值   音符不可以超出拍號的長度（MIDI 轉出的譜常有；那一小節會被拉長）
  空白聲部   整個聲部沒有任何音高
  速度標記   每個有速度的小節，只有第一個聲部需要文字；其他聲部重複寫會疊在一起
  授權來源   沒有記錄授權或來源時提出警告（只警告，不算問題；見 docs/SCORES.md）

可自動修的
  速度文字重複：第一個聲部以外的 <direction> 只留 <sound tempo>（時間軸完全不變，畫面不再疊字）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lxml import etree

import omr
import works
from xmlsafe import PARSER


def load(path: Path):
    return etree.parse(str(path), PARSER).getroot()


def measure_plan(part):
    """逐小節的 divisions 與拍號應有長度。"""
    div, beats, bt = 1, 4, 4
    out = []
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            d = a.findtext("divisions")
            if d:
                div = int(d)
            t = a.find("time")
            if t is not None:
                beats, bt = int(t.findtext("beats")), int(t.findtext("beat-type"))
        out.append(int(round(div * 4 * beats / bt)))
    return out


def over_full(part) -> list[int]:
    """音符超出小節長度的小節（1-based）：起點在小節外，或是延伸過小節線。"""
    plan, bad = measure_plan(part), []
    for mi, m in enumerate(part.findall("measure")):
        nominal = plan[mi]
        pos = last = 0
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
                grace = e.find("grace") is not None
                if e.find("rest") is None and (start >= nominal or (not grace and start + dur > nominal)):
                    bad.append(mi + 1)
                    break
    return bad


def tempo_marks(part) -> dict[int, list]:
    """每個小節裡「有速度或文字」的 direction。"""
    out = {}
    for mi, m in enumerate(part.findall("measure")):
        ds = [d for d in m.findall("direction")
              if d.find("direction-type/words") is not None or d.find("direction-type/metronome") is not None]
        if ds:
            out[mi] = ds
    return out


def text_of(d) -> str:
    words = [w.text or "" for w in d.findall("direction-type/words")]
    metro = [f"{mm.findtext('beat-unit')}={mm.findtext('per-minute')}" for mm in d.findall("direction-type/metronome")]
    return " ".join(words + metro).strip()


def strip_duplicate_tempo(carrier_texts: dict[int, set], part) -> int:
    """把和第一個聲部重複的速度文字拿掉，只留 <sound tempo>（時間軸不變）。回傳拿掉幾個。"""
    removed = 0
    for mi, m in enumerate(part.findall("measure")):
        for d in list(m.findall("direction")):
            txt = text_of(d)
            if not txt or txt not in carrier_texts.get(mi, set()):
                continue
            snd = d.find("sound")
            tempo = snd.get("tempo") if snd is not None else None
            m.remove(d)
            if tempo:   # 速度值留著：<sound> 可以直接掛在 measure 底下
                keep = etree.Element("sound")
                keep.set("tempo", tempo)
                m.insert(0, keep)
            removed += 1
    return removed


def check_work(work_slug: str, fix: bool) -> int:
    pieces = works.pieces_of(work_slug)
    if not pieces:
        print(f"  {work_slug}: 沒有分譜")
        return 1
    pieces = sorted(pieces, key=lambda pm: pm[1].get("order", 0))
    info = []
    for pdir, meta in pieces:
        root = load(pdir / "score.musicxml")
        part = root.find("part")
        info.append({"dir": pdir, "meta": meta, "root": root, "part": part,
                     "bars": len(part.findall("measure")), "notes": len(part.findall(".//note/pitch"))})
    problems = 0
    bars = {i["bars"] for i in info}
    if len(bars) > 1:
        print(f"  ✗ 各聲部小節數不一致：" + "、".join(f"{i['meta'].get('part')} {i['bars']}" for i in info))
        problems += 1
    for i in info:
        bad = over_full(i["part"])
        if bad:
            print(f"  ✗ {i['meta'].get('part')}：{len(bad)} 個小節的音符超出小節長度（前幾個：{bad[:6]}）")
            problems += 1
        if i["notes"] == 0:
            print(f"  ✗ {i['meta'].get('part')}：整個聲部沒有音高")
            problems += 1

    # 速度文字：只有第一個聲部需要，其他重複的會疊在畫面上
    carrier = info[0]
    carrier_texts = {mi: {text_of(d) for d in ds if text_of(d)} for mi, ds in tempo_marks(carrier["part"]).items()}
    dupes = []
    for i in info[1:]:
        n = sum(1 for mi, ds in tempo_marks(i["part"]).items()
                for d in ds if text_of(d) and text_of(d) in carrier_texts.get(mi, set()))
        if n:
            dupes.append((i, n))
    if dupes:
        total = sum(n for _, n in dupes)
        where = "、".join(f"{i['meta'].get('part')} {n}" for i, n in dupes)
        if fix:
            for i, _ in dupes:
                removed = strip_duplicate_tempo(carrier_texts, i["part"])
                works.write_piece_score(i["dir"], etree.tostring(i["root"], xml_declaration=True, encoding="UTF-8", pretty_print=True))
                print(f"  ✓ {i['meta'].get('part')}：拿掉 {removed} 個重複的速度文字（原檔備份在 backups/）")
        else:
            print(f"  ! 速度文字重複 {total} 個（{where}）— 用 --fix 只留第一個聲部的文字，速度值不動")
            problems += 1
    missing = [i["meta"].get("part") for i in info
               if not all((i["meta"].get(k) or omr.provenance(i["root"]).get(k)) for k in ("rights", "source"))]
    if missing:
        print(f"  ! 沒有授權或來源記錄：{'、'.join(str(m) for m in missing)}（見 docs/SCORES.md）")
    if not problems:
        print(f"  ✓ {len(info)} 個聲部、{info[0]['bars']} 小節，沒有發現問題")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", help="只檢查這個作品（名稱或 slug）")
    ap.add_argument("--fix", action="store_true", help="修掉可以自動修的問題（會先備份）")
    args = ap.parse_args()

    all_works = works.list_works()
    if args.work:
        want = works.slug(args.work)
        all_works = [w for w in all_works if w["slug"] == want or w["work"] == args.work]
        if not all_works:
            raise SystemExit(f"找不到作品 {args.work!r}")
    total = 0
    for w in all_works:
        print(f"{w['work']}（{w['slug']}）")
        total += check_work(w["slug"], args.fix)
    if total:
        print(f"\n共 {total} 個問題。時值超出小節的譜不要拿來練習：那些小節會被拉長，各聲部會對不齊。")
    raise SystemExit(1 if total and not args.fix else 0)


if __name__ == "__main__":
    main()
