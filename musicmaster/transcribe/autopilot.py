#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase1 一键扒谱(去专家在场)+ 多引擎注册表(保留所有优质模型,用户可选)。
串联:L1 quality_gate → L2 选引擎/定调 → 转录 → L3 confidence(仅单音)→ 渲染 → 报告.md

引擎注册表(--engine,全部保留,默认 crepe):
  crepe        单旋律(CREPE):人声/单线条乐器最稳健,出简谱 + 逐音置信度。默认。
  basic-pitch  通用复音(Apache):乐器/和弦兜底;低质音频上比 ByteDance 稳。
  bytedance    钢琴复音(高分辨率,SOTA):仅干净 44.1k 真钢琴;低质/OOD 会幻觉假和弦(实测 8kHz 吐 98 音)。
设计哲学:不替用户硬选模型——给智能默认 + L1 质量门 + L3 存疑标注,用户按料子自行切引擎。

用法: PYTHONUTF8=1 PY autopilot.py IN.wav --out DIR [--engine crepe|basic-pitch|bytedance] [--key C]
"""
from __future__ import annotations
import argparse, importlib, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

from . import quality as quality_gate
from . import confidence
from . import transcribe2
from . import piano_bytedance       # torch/模型在函数内懒加载,不拖慢其它引擎
eng_t = importlib.import_module("musicmaster.core.transcribe")
eng_r = importlib.import_module("musicmaster.core.render")

ENGINES = {
    "crepe": "单旋律 CREPE(最稳健;人声/单线条默认,出简谱+置信度)",
    "basic-pitch": "通用复音 basic-pitch(乐器/和弦兜底;低质上比 ByteDance 稳)",
    "bytedance": "钢琴复音 ByteDance(SOTA;仅干净 44.1k 真钢琴,低质/OOD 会幻觉)",
}


def run_engine(engine, inp, fmin, fmax):
    if engine == "crepe":
        target, _ = transcribe2.transcribe(inp, fmin=fmin, fmax=fmax, snap="off", f0_method="crepe")
        return target
    if engine == "basic-pitch":
        return eng_t.transcribe(inp, instrument=True)
    if engine == "bytedance":
        return piano_bytedance.transcribe(inp, out_mid=None)
    raise ValueError("unknown engine: " + engine)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", default="crepe", choices=list(ENGINES),
                    help="转录引擎(保留全部,默认 crepe);见文件头注释")
    ap.add_argument("--poly", action="store_true", help="[兼容] 等价 --engine basic-pitch")
    ap.add_argument("--key", default=None, help="如 C;不给则稳健自动定调")
    ap.add_argument("--fmin", default="C2")
    ap.add_argument("--fmax", default="C6")
    a = ap.parse_args(argv)
    engine = "basic-pitch" if (a.poly and a.engine == "crepe") else a.engine
    is_mono = engine == "crepe"
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rep = ["# 一键扒谱报告(autopilot)\n", f"> 源:`{Path(a.input).name}`  引擎:`{engine}`\n"]

    # ===== L1 入口质量门 =====
    q = quality_gate.assess(a.input)
    qr = quality_gate.human_report(q)
    print(qr)
    rep.append("## ① 录音体检(L1 质量门)\n```\n" + qr + "\n```\n")
    if q["gate"] == "block":
        rep.append("> ⚠️ 质量门判定**太差**:结果会大段不可靠,优先按上面修法重录。\n")
        if engine == "bytedance":
            warn = "⚠️ 低质音频对 ByteDance 是 OOD,会幻觉假和弦;建议改 --engine crepe 或换好录音。"
            print(warn); rep.append("> " + warn + "\n")

    # ===== L2 选引擎转录 =====
    target = run_engine(engine, a.input, a.fmin, a.fmax)
    notes = target["notes"]
    (out / "notes.json").write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")

    # ===== 定调(稳健,不被少数坏音带偏)=====
    if a.key:
        key = a.key
    else:
        tonic, _m, _s = confidence._detect_scale_robust(notes)
        key = confidence.PC[tonic]

    # ===== 渲染:MIDI + 五线谱 +(单音才出)简谱 =====
    eng_t.target_to_pretty_midi(target).write(str(out / "out.mid"))
    mxl = eng_t.target_to_musicxml(target, out / "out.musicxml", key_name=key)
    fmts = ("staff", "jianpu") if is_mono else ("staff",)
    try:
        rr = eng_r.render(str(mxl), str(out), formats=fmts, jianpu_format="pdf")
        rendered = {k: Path(v).name for k, v in rr.items()}
    except Exception as e:
        rendered = {"err": repr(e)[:120]}
        print("RENDER-ERR", rendered["err"])

    # ===== L3 置信度(复音简谱无意义,仅单音出)=====
    conf_txt = ""
    if is_mono:
        r = confidence.assess(a.input, notes, key=key, gate_tier=q["tier"], fmin=a.fmin, fmax=a.fmax)
        conf_txt = confidence.human_report(r)
        print("\n" + conf_txt)
        (out / "confidence.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    rep.append(f"## ② 引擎 + 定调(L2)\n- 引擎:{ENGINES[engine]}\n- 调:**1={key}**\n- 音符数:{len(notes)}\n")
    if conf_txt:
        rep.append("## ③ 扒谱可信度(L3)\n```\n" + conf_txt + "\n```\n")
    flist = ["staff.svg 五线谱(浏览器开)", "out.mid 回放核对", "out.musicxml", "notes.json"]
    if is_mono:
        flist += ["jianpu.pdf 简谱", "confidence.json 逐音可信度"]
    rep.append("## ④ 文件清单\n" + "\n".join(f"- {x}" for x in flist) + "\n")
    rep.append("\n> 先看体检档位,再看可信度【存疑段】——只核对那几处,其余放心。复音引擎(basic-pitch/bytedance)只出五线谱。\n")
    (out / "报告.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n=> 一站式报告:", out / "报告.md")
    print("=> 渲染:", rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
