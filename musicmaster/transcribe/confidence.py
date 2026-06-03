#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase1·L3 置信度系统:给每个扒出的音标"可信度",不确定的地方自己举手,
让小白不用懂算法也知道"哪几个音别全信、去听一下"。

关键(来自数据探查 Entry#26):坏段上算法会【自信地、异口同声地错】——
CREPE 自信度、pyin 有声、两算法一致率全失灵;唯一暴露它的是"乐理讲不通"。
所以可信度 = 主信号【乐理合理性(成串调外/漂移)】× 副信号【双算法分歧】× 跟踪自信 × L1全局门。

诚实红线:调外 ≠ 错(真曲子有真变化音)。只对【成串】调外报"存疑",单个变化音不误伤;
措辞一律"可能扒错、也可能你真弹了变化音,请听 out.mid 核对",绝不武断判"错"。

对外 API:assess(path, notes, key=None, gate_tier=None) -> dict
CLI: PYTHONUTF8=1 PY confidence.py AUDIO [--notes notes.json] [--key C] [--json]
     不给 --notes 则自动用 transcribe2(CREPE)先扒。
"""
from __future__ import annotations
import argparse, importlib, json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import numpy as np
import librosa

from . import transcribe2  # detect_key / transcribe

PC = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJ_DEG = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7",
           1: "#1", 3: "#2", 6: "#4", 8: "#5", 10: "b7"}


def _frame_tracks(path, fmin="C2", fmax="C6", hop_ms=10.0):
    """CREPE(16k) + pyin(原生)两条 f0,统一到 10ms 时间轴。
    返回 t, crepe_midi, crepe_conf, pyin_midi(插值), pyin_voiced(插值)。"""
    lo = float(librosa.note_to_hz(fmin)); hi = float(librosa.note_to_hz(fmax))
    y16, _ = librosa.load(str(path), sr=16000, mono=True)
    import crepe
    t, f, c, _ = crepe.predict(np.ascontiguousarray(y16, dtype="float32"), 16000,
                               viterbi=False, step_size=int(round(hop_ms)), verbose=0)
    cm = librosa.hz_to_midi(np.where(f <= 0, 1.0, f))
    y, sr = librosa.load(str(path), sr=None, mono=True)
    hop = int(round(sr * hop_ms / 1000.0))
    f0p, _vf, vp = librosa.pyin(y, fmin=lo, fmax=hi, sr=sr, frame_length=2048, hop_length=hop)
    tp = np.arange(len(f0p)) * hop / sr
    pm = np.interp(t, tp, librosa.hz_to_midi(np.where(np.isnan(f0p), 1.0, f0p)))
    pv = np.interp(t, tp, np.nan_to_num(vp, nan=0.0))
    return t, cm, np.asarray(c, float), pm, pv


def _out_of_key_runs(in_scale):
    """每个音所在【连续调外段】长度(调内=0)。这是抓'一起漂移'的主信号。"""
    n = len(in_scale); run = [0] * n; i = 0
    while i < n:
        if not in_scale[i]:
            j = i
            while j < n and not in_scale[j]:
                j += 1
            for k in range(i, j):
                run[k] = j - i
            i = j
        else:
            i += 1
    return run


def _detect_scale_robust(notes):
    """挑"调外时长最少"的大调音集(对少数漂移/真变化音稳健,比 K-S 不易被坏音带偏)。
    大调与其关系小调共用同一音集,对"是否调内"判定等价,故只需定音集、取该集合的大调主音作展示。"""
    dur = np.zeros(12)
    for n in notes:
        dur[int(n["midi"]) % 12] += max(n["end_s"] - n["start_s"], 1e-3)
    MAJ = [0, 2, 4, 5, 7, 9, 11]
    best = None
    for tonic in range(12):
        scale = {(tonic + s) % 12 for s in MAJ}
        out = sum(d for pc, d in enumerate(dur) if pc not in scale)
        if best is None or out < best[0]:
            best = (out, tonic)
    return best[1], "maj", MAJ


def assess(path, notes, key=None, gate_tier=None, fmin="C2", fmax="C6"):
    notes = sorted(notes, key=lambda x: x["start_s"])
    N = len(notes)
    if N == 0:
        return {"overall": 0, "levels": {}, "passages": [], "notes": [], "key": None}

    # ---- 1. 定调(给了就用,否则 K-S 自动)----
    if key:
        k = key.strip()
        name = k[:2] if (len(k) >= 2 and k[1] in "#b") else k[:1]
        FLAT = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#", "FB": "E", "CB": "B"}
        name = FLAT.get(name.upper(), name[0].upper() + name[1:])
        tonic = PC.index(name) if name in PC else 0
        is_min = ("min" in k.lower()) or k.lower().rstrip(":").endswith("m")
        mode = "min" if is_min else "maj"
        steps = [0, 2, 3, 5, 7, 8, 10] if is_min else [0, 2, 4, 5, 7, 9, 11]
    else:
        tonic, mode, steps = _detect_scale_robust(notes)
    scale_pcs = {(tonic + s) % 12 for s in steps}
    in_scale = [(int(n["midi"]) % 12) in scale_pcs for n in notes]
    run_len = _out_of_key_runs(in_scale)

    # ---- 2. 帧级两算法 + 自信(用于副信号)----
    t, cm, cc, pm, pv = _frame_tracks(path, fmin=fmin, fmax=fmax)

    gmul = {"太差": 0.85, "差": 0.92}.get(gate_tier, 1.0)

    out_notes = []
    for i, n in enumerate(notes):
        a, b = float(n["start_s"]), float(n["end_s"])
        fm = (t >= a) & (t < b)
        reasons = []
        # 自信
        cconf = float(np.mean(cc[fm])) if fm.any() else 0.0
        # 双算法一致(仅两者都有声的帧)
        both = fm & (cc > 0.5) & (pv > 0.5)
        agree = float(np.mean(np.abs(cm[both] - pm[both]) <= 0.5)) if both.any() else None

        # —— 主信号:乐理合理性 ——
        if in_scale[i]:
            c_key = 1.0
        elif run_len[i] >= 3:
            c_key = 0.25; reasons.append("连续调外(疑似整段被拖飘)")
        elif run_len[i] == 2:
            c_key = 0.5; reasons.append("成对调外")
        else:
            c_key = 0.72; reasons.append("单个调外(也可能是真变化音)")
        # —— 副信号:双算法分歧 ——
        if agree is None:
            c_agree = 1.0
        else:
            c_agree = 0.3 + 0.7 * agree
            if agree < 0.8:
                reasons.append("两算法分歧")
        # —— 跟踪自信 ——
        c_self = 1.0 if cconf >= 0.30 else 0.6
        if cconf < 0.30:
            reasons.append("跟踪自信低")

        conf = max(0.0, min(1.0, c_key * c_agree * c_self * gmul))
        level = "高" if conf >= 0.70 else ("中" if conf >= 0.40 else "低")
        deg = MAJ_DEG.get((int(n["midi"]) - tonic) % 12, "?")
        out_notes.append({"i": i, "midi": int(n["midi"]), "start_s": round(a, 3), "end_s": round(b, 3),
                          "deg": deg, "conf": round(conf, 3), "level": level,
                          "in_key": in_scale[i], "crepe_conf": round(cconf, 3),
                          "agree": (round(agree, 3) if agree is not None else None),
                          "reasons": reasons})

    # ---- 3. 汇总 + 存疑段合并 ----
    overall = round(100.0 * float(np.mean([x["conf"] for x in out_notes])), 1)
    levels = {"高": 0, "中": 0, "低": 0}
    for x in out_notes:
        levels[x["level"]] += 1
    passages = []
    cur = None
    for x in out_notes:
        if x["level"] in ("低", "中"):
            if cur is None:
                cur = {"start_s": x["start_s"], "end_s": x["end_s"], "n": 1, "reasons": set(x["reasons"]), "min_conf": x["conf"]}
            else:
                cur["end_s"] = x["end_s"]; cur["n"] += 1; cur["reasons"] |= set(x["reasons"]); cur["min_conf"] = min(cur["min_conf"], x["conf"])
        else:
            if cur:
                passages.append(cur); cur = None
    if cur:
        passages.append(cur)
    for p in passages:
        p["reasons"] = sorted(p["reasons"])

    return {"overall": overall, "levels": levels, "passages": passages,
            "key": f"{PC[tonic]}{'小调' if mode.startswith('min') else '大调'}",
            "gate_tier": gate_tier, "notes": out_notes}


def human_report(r):
    L = []
    lv = r["levels"]
    L.append(f"扒谱可信度:整体 {r['overall']}/100   调={r['key']}"
             + (f"   (录音体检:{r['gate_tier']},已整体打折)" if r.get("gate_tier") in ("太差", "差") else ""))
    L.append(f"  逐音:✅高可信 {lv.get('高',0)} | 🟡中 {lv.get('中',0)} | ⚠️低可信 {lv.get('低',0)}（共 {sum(lv.values())} 音）")
    if r["passages"]:
        L.append("  ⚠️ 存疑段(建议听 out.mid 这几处核对——可能扒错,也可能你真弹了变化音):")
        for p in r["passages"]:
            why = "、".join(p["reasons"]) or "可信度偏低"
            L.append(f"    {p['start_s']:.1f}–{p['end_s']:.1f}s  ({p['n']}音,最低可信 {p['min_conf']:.2f})  ← {why}")
    else:
        L.append("  无存疑段,整体可放心。")
    # 带标注简谱(低可信音用 ‹›? 包注)
    seq = []
    for x in r["notes"]:
        s = x["deg"]
        if x["level"] == "低":
            s = f"‹{s}›?"
        elif x["level"] == "中":
            s = f"{s}?"
        seq.append(s)
    L.append("  带标注简谱(‹›?=低可信,需核对):")
    L.append("    " + " ".join(seq))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--notes", default=None, help="主转录 notes.json;不给则自动用 CREPE 扒")
    ap.add_argument("--key", default=None, help="如 C;不给则 K-S 自动定调")
    ap.add_argument("--gate-tier", default=None, help="L1 体检档位(太差/差/可用/好);不给则自动调 quality_gate")
    ap.add_argument("--fmin", default="C2"); ap.add_argument("--fmax", default="C6")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.notes:
        notes = json.loads(Path(a.notes).read_text(encoding="utf-8"))["notes"]
    else:
        target, _info = transcribe2.transcribe(a.input, fmin=a.fmin, fmax=a.fmax,
                                                snap="off", f0_method="crepe")
        notes = target["notes"]

    tier = a.gate_tier
    if tier is None:
        try:
            qg = importlib.import_module("musicmaster.transcribe.quality")
            tier = qg.assess(a.input)["tier"]
        except Exception as e:
            print("（质量门跳过:", repr(e)[:80], "）")

    r = assess(a.input, notes, key=a.key, gate_tier=tier, fmin=a.fmin, fmax=a.fmax)
    print(human_report(r))
    if a.json:
        print("\nJSON " + json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
