"""從（合併後的）MusicXML 算出播放用的時間軸，在伺服器端做並快取，瀏覽器就不必載入整份譜分析。

輸出格式（JSON，和前端 restoreTimeline() 對應）：
  nMeasures, totalBeats, hasTempo,
  tempo[m]            每小節 ♩/分（沒有標記的沿用前一個；全曲都沒有則 100）
  timeSig[m]          [beats, beat-type]
  measureStartBeat[m] 小節起點（四分音符拍）
  measureTs[m]        小節起點（全音符為單位的分數）[分子, 分母]
  measureFirstStep[m] 該小節第一個 step 的索引
  steps[k]            [beat, measure, 時間戳分子, 分母, [[midi, 拍數(含延音線), 聲部], ...]]

時間規則比照 OSMD 的迭代器：每個有音符或休止符起點的時間點是一個 step；
和弦同時發聲；延音線串起來的音只在第一個音發聲、時值加總；倚音不發聲；
小節長度取所有聲部實際填滿的最長值（全空時用拍號長度）；速度取該小節最後讀到的標記。
極少數 OSMD 對格式有問題的譜（例如 MIDI 轉出、聲部溢出小節）會算出不同結果，
所以瀏覽器第一次載入後會在空檔用 OSMD 核對一次，有差異就改用 OSMD 的版本並回傳伺服器快取。
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

from lxml import etree

from xmlsafe import PARSER

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _midi(note, chromatic: int) -> int | None:
    p = note.find("pitch")
    if p is None:
        return None
    step = p.findtext("step")
    if step not in STEPS:
        return None
    try:
        octave = int(p.findtext("octave"))
        alter = round(float(p.findtext("alter") or 0))
    except (TypeError, ValueError):
        return None
    return (octave + 1) * 12 + STEPS[step] + alter + chromatic


def _read_part(part) -> list[dict]:
    div = 1
    beats, beat_type = 4, 4
    chromatic = 0
    out = []
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            d = a.findtext("divisions")
            if d and d.strip().isdigit() and int(d) > 0:
                div = int(d)
            t = a.find("time")
            if t is not None:
                try:
                    beats, beat_type = int(t.findtext("beats")), int(t.findtext("beat-type"))
                except (TypeError, ValueError):
                    pass  # 例如 "3+2"：沿用前一個拍號
            tr = a.find("transpose")
            if tr is not None:
                try:
                    chromatic = int(tr.findtext("chromatic") or 0) + 12 * int(tr.findtext("octave-change") or 0)
                except ValueError:
                    pass
        tempo = None
        for snd in m.iter("sound"):  # 一小節有好幾個速度標記時取最後一個（和 OSMD 相同）
            v = snd.get("tempo")
            if v:
                try:
                    tempo = float(v)
                except ValueError:
                    pass
        pos = last = mx = Fraction(0)
        events = []
        for el in m:
            tag = el.tag
            if tag == "backup":
                pos -= Fraction(int(el.findtext("duration") or 0), div)
            elif tag == "forward":
                pos += Fraction(int(el.findtext("duration") or 0), div)
                mx = max(mx, pos)
            elif tag == "note":
                grace = el.find("grace") is not None
                chord = el.find("chord") is not None
                dur = Fraction(int(el.findtext("duration") or 0), div)
                onset = last if chord else pos
                if not chord:
                    last = pos
                    if not grace:
                        pos += dur
                    mx = max(mx, pos)
                if grace:
                    continue
                silent = el.find("rest") is not None or el.find("cue") is not None
                ties = {t.get("type") for t in el.findall("tie")}
                events.append((onset, dur, None if silent else _midi(el, chromatic),
                               "start" in ties, "stop" in ties, el.findtext("voice") or "1", el.findtext("staff") or "1"))
        out.append({"events": events, "fill": mx, "nominal": Fraction(4 * beats, beat_type), "tempo": tempo, "ts": [beats, beat_type]})
    return out


def compute(xml: bytes) -> dict:
    root = etree.fromstring(xml, PARSER)
    parts = root.findall("part")
    if not parts:
        raise ValueError("MusicXML 沒有 part")
    per_part = [_read_part(p) for p in parts]
    n = max(len(pp) for pp in per_part)

    durations, tempo, time_sig = [], [], []
    current = 0.0
    for mi in range(n):
        infos = [pp[mi] for pp in per_part if mi < len(pp)]
        ref = infos[0]
        fill = max(i["fill"] for i in infos)
        durations.append(fill if fill > 0 else ref["nominal"])
        t = next((i["tempo"] for i in reversed(infos) if i["tempo"]), None)  # 後面的聲部覆蓋前面的（OSMD 依聲部順序讀）
        if t:
            current = t
        tempo.append(current)
        time_sig.append(ref["ts"])

    starts = [Fraction(0)]
    for d in durations:
        starts.append(starts[-1] + d)

    step_notes: dict[Fraction, list] = {}
    step_measure: dict[Fraction, int] = {}
    for pi, pp in enumerate(per_part):
        open_ties: dict[tuple, list] = {}
        for mi, info in enumerate(pp):
            base = starts[mi]
            for onset, dur, midi, tie_start, tie_stop, voice, staff in info["events"]:
                t_abs = base + onset
                notes = step_notes.setdefault(t_abs, [])
                step_measure.setdefault(t_abs, mi)
                if midi is None:
                    continue
                # 延音線用「譜表＋音高」配對：MIDI 轉出的譜常常前後兩個音的 voice 編號不一樣（或缺）
                key = (staff, midi)
                if tie_stop and key in open_ties:  # 延音線的後半：加到開頭那個音的時值，不另外發聲
                    open_ties[key][1] += dur
                    if not tie_start:
                        del open_ties[key]
                    continue
                rec = [midi, dur, pi]
                notes.append(rec)
                if tie_start:
                    open_ties[key] = rec
                else:
                    open_ties.pop(key, None)

    steps = []
    first_step: list[int | None] = [None] * n
    total = starts[n]
    for t_abs in sorted(step_notes):
        mi = step_measure[t_abs]
        if first_step[mi] is None:
            first_step[mi] = len(steps)
        whole = t_abs / 4
        notes = step_notes[t_abs]
        steps.append([float(t_abs), mi, whole.numerator, whole.denominator, [[r[0], float(r[1]), r[2]] for r in notes]])
        for r in notes:
            total = max(total, t_abs + r[1])
    nxt = len(steps)
    for mi in range(n - 1, -1, -1):
        if first_step[mi] is None:
            first_step[mi] = nxt
        else:
            nxt = first_step[mi]

    has_tempo = any(t > 0 for t in tempo)
    return {
        "v": 2,
        "verified": False,   # 瀏覽器用 OSMD 核對過並回傳後才是 True
        "nMeasures": n,
        "totalBeats": float(total),
        "hasTempo": has_tempo,
        "tempo": [t if t > 0 else 100 for t in tempo],
        "timeSig": time_sig,
        "measureStartBeat": [float(s) for s in starts[:n]],
        "measureTs": [[(s / 4).numerator, (s / 4).denominator] for s in starts[:n]],
        "measureFirstStep": first_step,
        "steps": steps,
    }


def compute_json(xml: bytes) -> bytes:
    return json.dumps(compute(xml), separators=(",", ":")).encode()


if __name__ == "__main__":
    data = compute(Path(sys.argv[1]).read_bytes())
    print("measures", data["nMeasures"], "steps", len(data["steps"]), "totalBeats", data["totalBeats"])
