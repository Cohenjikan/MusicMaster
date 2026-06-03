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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

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
    rendered: dict = {}
    rc = 0
    # 任一阶段失败都不应让整页崩 + 吞掉报告:各阶段独立 try,报告.md 在 finally 一定落盘(修审计 H3/H4)
    try:
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
        try:
            target = run_engine(engine, a.input, a.fmin, a.fmax)
        except Exception as e:
            msg = (f"引擎「{engine}」转录失败:{type(e).__name__}: {e}\n"
                   f"提示:basic-pitch 需 TensorFlow 2.15 干净环境;bytedance 需 torch + 首次联网下权重。"
                   f"快速上手请用默认引擎 crepe(人声/单旋律最佳)。")
            print("ENGINE-ERR", msg)
            rep.append("## ② 转录失败\n```\n" + msg + "\n```\n")
            return 1
        notes = target["notes"]
        (out / "notes.json").write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")

        # ===== 定调(稳健,不被少数坏音带偏)=====
        if a.key:
            key = a.key
        else:
            try:
                tonic, _m, _s = confidence._detect_scale_robust(notes)
                key = confidence.PC[tonic]
            except Exception:
                key = "C"

        # ===== 渲染:MIDI + 五线谱 +(单音才出)简谱(各步独立兜底)=====
        try:
            eng_t.target_to_pretty_midi(target).write(str(out / "out.mid"))
        except Exception as e:
            print("MIDI-ERR", repr(e)); rep.append(f"> ⚠️ MIDI 生成失败:{type(e).__name__}: {e}\n")
        try:
            mxl = eng_t.target_to_musicxml(target, out / "out.musicxml", key_name=key)
            fmts = ("staff", "jianpu") if is_mono else ("staff",)
            try:
                rr = eng_r.render(str(mxl), str(out), formats=fmts, jianpu_format="pdf")
                rendered = {k: Path(v).name for k, v in rr.items()}
            except Exception as e:
                rendered = {"err": repr(e)[:120]}
                print("RENDER-ERR", rendered["err"])
        except Exception as e:
            print("MUSICXML-ERR", repr(e)); rep.append(f"> ⚠️ MusicXML 生成失败:{type(e).__name__}: {e}\n")

        # ===== L3 置信度(复音简谱无意义,仅单音出)=====
        conf_txt = ""
        if is_mono:
            try:
                r = confidence.assess(a.input, notes, key=key, gate_tier=q["tier"], fmin=a.fmin, fmax=a.fmax)
                conf_txt = confidence.human_report(r)
                print("\n" + conf_txt)
                (out / "confidence.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print("CONF-ERR", repr(e)); conf_txt = f"(置信度评估失败:{type(e).__name__}: {e})"

        rep.append(f"## ② 引擎 + 定调(L2)\n- 引擎:{ENGINES[engine]}\n- 调:**1={key}**\n- 音符数:{len(notes)}\n")
        if conf_txt:
            rep.append("## ③ 扒谱可信度(L3)\n```\n" + conf_txt + "\n```\n")
        # 文件清单按【实际产物】动态生成,避免谎报不存在的 jianpu.pdf(修审计 M3)
        flist = []
        for fn, label in (("staff.svg", "staff.svg 五线谱(浏览器开)"), ("out.mid", "out.mid 回放核对"),
                          ("out.musicxml", "out.musicxml"), ("notes.json", "notes.json")):
            if (out / fn).is_file():
                flist.append(label)
        if (out / "jianpu.pdf").is_file():
            flist.append("jianpu.pdf 简谱")
        elif (out / "jianpu.ly").is_file():
            flist.append("jianpu.ly 简谱源(装 LilyPond 后可渲染成图)")
        if (out / "confidence.json").is_file():
            flist.append("confidence.json 逐音可信度")
        rep.append("## ④ 文件清单\n" + "\n".join(f"- {x}" for x in flist) + "\n")
        rep.append("\n> 先看体检档位,再看可信度【存疑段】——只核对那几处,其余放心。复音引擎(basic-pitch/bytedance)只出五线谱。\n")
        print("=> 渲染:", rendered)
    finally:
        (out / "报告.md").write_text("\n".join(rep), encoding="utf-8")
        print("\n=> 一站式报告:", out / "报告.md")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
