#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MusicMaster — Gradio 单页 Web GUI。

四个标签页对应四大能力:
  互转       简谱 ⇄ 五线谱 双向无损转换 / 导入            (CPU,本进程)
  扒谱       音频 → 五线谱 + 简谱 + 逐音可信度(autopilot)  (CPU,本进程)
  分离       混音 → 人声/伴奏/去和声/降噪                  (GPU,子进程 → 分离 venv)
  修音换音色 跑调清唱 → 在调 + 干净 + 仍是你(两段式)       (GPU,子进程 → 修音/换音色 venv)

运行:
  pip install -r requirements-core.txt   # 含 gradio
  python app.py                          # 浏览器开 http://127.0.0.1:7860

GPU 标签页(分离 / 修音换音色)需另装对应 GPU venv 并设环境变量,见 README;
未配置时这两页会给出友好提示,不影响 CPU 两页(互转/扒谱)正常使用。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import gradio as gr

REPO = Path(__file__).resolve().parent
OUTPUT = REPO / "output"
OUTPUT.mkdir(exist_ok=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def _job_dir(prefix: str) -> Path:
    d = OUTPUT / f"{prefix}_{int(time.time() * 1000)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _svg_html(svg_path: Path) -> str:
    """把 Verovio 五线谱 SVG 内嵌进可滚动容器。"""
    if not svg_path or not Path(svg_path).is_file():
        return "<i>(无五线谱)</i>"
    svg = Path(svg_path).read_text(encoding="utf-8")
    return f'<div style="overflow:auto;max-height:520px;border:1px solid #ddd;padding:8px;background:#fff">{svg}</div>'


def _subprocess_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _sep_python() -> str | None:
    """分离 venv(.venv-sep)的 python;优先级:环境变量 > paths.local.json > vendor 默认。"""
    from musicmaster import _paths
    p = _paths.resolve("sep_python", "MUSICMASTER_SEP_PYTHON", None)
    if p and Path(p).is_file():
        return p
    for cand in (REPO / "vendor" / ".venv-sep" / "Scripts" / "python.exe",
                 REPO / ".venv-sep" / "Scripts" / "python.exe"):
        if cand.is_file():
            return str(cand)
    return None


# ─────────────────────────────── 互转 ─────────────────────────────── #
def do_convert(file, direction, key):
    if file is None:
        return "请先上传文件。", None, None
    src = Path(file)
    d = _job_dir("convert")
    try:
        from musicmaster.convert import convert as cv
        if direction == "简谱 → 五线谱(MusicXML)":
            score = cv.load_any(src)
            out = d / (src.stem + ".musicxml")
            score.write("musicxml", fp=str(out))
            # 顺带渲染五线谱预览(core.__init__ 把 render 函数绑成了包属性,
            # 必须从子模块直接 import 函数本身,不能写 core.render 当模块用)
            from musicmaster.core.render import render as render_score
            rr = render_score(str(out), str(d), formats=("staff",))
            staff = rr.get("staff")
            return f"✅ 简谱 → 五线谱 MusicXML\n{out}", _svg_html(staff) if staff else "", str(out)
        else:  # 五线谱 → 简谱
            score = cv.load_any(src)
            text = cv.score_to_jianpu(score, key_name=(key or None))
            out = d / (src.stem + ".jianpu")
            out.write_text(text, encoding="utf-8")
            return f"✅ 五线谱 → 简谱文本:\n\n{text}", "", str(out)
    except Exception as e:
        return f"❌ 转换失败:{type(e).__name__}: {e}", "", None


# ─────────────────────────────── 扒谱 ─────────────────────────────── #
def do_transcribe(audio, engine, key):
    if audio is None:
        return "请先上传清唱音频。", "", None, None
    d = _job_dir("transcribe")
    try:
        from musicmaster.transcribe import autopilot
        argv = [str(audio), "--out", str(d), "--engine", engine]
        if key and key.strip():
            argv += ["--key", key.strip()]
        autopilot.main(argv)
        report = (d / "报告.md").read_text(encoding="utf-8") if (d / "报告.md").is_file() else "(无报告)"
        staff = d / "staff.svg"
        downloads = [str(p) for p in (d / "notes.json", d / "out.mid", d / "out.musicxml",
                                       d / "jianpu.pdf", d / "confidence.json") if p.is_file()]
        return report, _svg_html(staff), downloads, None
    except Exception as e:
        hint = ""
        if engine in ("basic-pitch", "bytedance"):
            hint = (f"\n\n💡 引擎「{engine}」需专用环境(basic-pitch 需 TensorFlow 2.15 干净环境;"
                    f"bytedance 需 GPU)。快速上手请用默认引擎 **crepe**(人声/单旋律最佳)。详见 README。")
        return f"❌ 扒谱失败:{type(e).__name__}: {str(e)[:240]}{hint}", "", None, None


# ─────────────────────────────── 分离 ─────────────────────────────── #
def do_separate(audio, stages, denoise):
    if audio is None:
        return "请先上传混音音频。", None
    sep_py = _sep_python()
    if not sep_py:
        return ("⚠️ 未找到分离 venv(.venv-sep)。分离需 GPU 与 audio-separator,"
                "请按 README 安装并设环境变量 MUSICMASTER_SEP_PYTHON 指向其 python。"), None
    d = _job_dir("separate")
    cmd = [sep_py, "-m", "musicmaster.separate.pipeline", str(audio),
           "--stages", stages, "--denoise", denoise, "--out-dir", str(d)]
    try:
        r = subprocess.run(cmd, cwd=str(REPO), env=_subprocess_env(),
                           capture_output=True, text=True, timeout=1800)
        log = (r.stdout or "") + "\n" + (r.stderr or "")
        wavs = sorted(str(p) for p in d.rglob("*.wav"))
        if r.returncode != 0:
            return f"❌ 分离失败(returncode={r.returncode}):\n{log[-1200:]}", None
        return f"✅ 分离完成,产物 {len(wavs)} 个:\n" + "\n".join(wavs) + f"\n\n{log[-600:]}", wavs
    except Exception as e:
        return f"❌ 分离异常:{type(e).__name__}: {e}", None


# ─────────────────────────── 修音换音色 ─────────────────────────── #
def do_vocal(raw, ref, self_ref, correct_steps, voice_steps, voice_cfg):
    if not (raw and ref and self_ref):
        return "请上传三个文件:① 原始清唱 ② 去和声参考 ③ 你自己的清唱(音色锚)。", None, None
    from musicmaster.vocal import config as vcfg, pipeline as vpipe

    probs = vcfg.check_paths("correct") + vcfg.check_paths("voice")
    if probs:
        return ("⚠️ 修音/换音色环境未就绪(需 GPU venv + 权重):\n  - " + "\n  - ".join(probs)
                + "\n\n见 README 的「修音换音色(GPU)」设置。"), None, None
    d = _job_dir("vocal")
    try:
        res = vpipe.two_stage(raw, ref, self_ref, d,
                              correct_steps=int(correct_steps), voice_steps=int(voice_steps),
                              voice_cfg=float(voice_cfg))
        final = res.get("final")
        detail = "\n".join(f"{k}: {v}" for k, v in res.items())
        if final:
            msg = "✅ 两段式完成:\n" + detail
        else:
            # 子进程 returncode=0 但没产出 vc_*.wav 的边缘情况:别谎报成功(修审计 low)
            msg = ("⚠️ 修音完成,但换音色未产出成品(Seed-VC 没生成 vc_*.wav)。"
                   "请看运行窗口日志,检查 .venv-svc 与权重是否就绪。\n" + detail)
        return msg, (str(final) if final else None), (str(res["corrected_24k"]))
    except Exception as e:
        import traceback
        return f"❌ 失败:{type(e).__name__}: {e}\n\n{traceback.format_exc()[-800:]}", None, None


# ─────────────────────────────── UI ─────────────────────────────── #
def build() -> gr.Blocks:
    with gr.Blocks(title="MusicMaster") as demo:
        gr.Markdown(
            "# 🎵 MusicMaster\n"
            "一体化音乐处理:**分离** · **扒谱(五线谱/简谱)** · **简谱⇄五线谱互转** · **修音换音色**。\n"
            "> 互转 / 扒谱 为纯 CPU,开箱即用;分离 / 修音换音色 需 GPU venv(见 README)。"
        )

        with gr.Tab("🎼 互转(简谱⇄五线谱)"):
            gr.Markdown("上传 `.jianpu` 简谱文本 或 `.musicxml/.mid/.abc` 五线谱,双向无损转换。")
            with gr.Row():
                cv_file = gr.File(label="上传文件(.jianpu / .musicxml / .mid / .abc)")
                with gr.Column():
                    cv_dir = gr.Radio(["简谱 → 五线谱(MusicXML)", "五线谱 → 简谱"],
                                      value="简谱 → 五线谱(MusicXML)", label="方向",
                                      info="简谱=数字谱(1234567);五线谱=MusicXML/MIDI/ABC")
                    cv_key = gr.Textbox(label="主音(调号 1=哪个音)", value="",
                                        info="仅「转简谱」时用:填该曲主音,如 G=G大调;留空则自动判定")
                    cv_btn = gr.Button("转换", variant="primary")
            cv_out = gr.Textbox(label="结果", lines=8)
            cv_svg = gr.HTML(label="五线谱预览")
            cv_dl = gr.File(label="下载产物")
            cv_btn.click(do_convert, [cv_file, cv_dir, cv_key], [cv_out, cv_svg, cv_dl])

        with gr.Tab("🎤 扒谱(音频→谱)"):
            gr.Markdown("上传**清唱/单旋律**音频 → 五线谱 + 简谱 + 逐音可信度。"
                        "引擎:crepe(单旋律最优)/ basic-pitch(复音兜底)/ bytedance(干净钢琴)。")
            with gr.Row():
                tr_audio = gr.Audio(label="上传音频", type="filepath")
                with gr.Column():
                    tr_engine = gr.Radio(
                        [("crepe(单旋律·人声/哼唱最稳·默认)", "crepe"),
                         ("basic-pitch(通用复音·乐器和弦兜底)", "basic-pitch"),
                         ("bytedance(钢琴专用·SOTA·仅干净44.1k真钢琴)", "bytedance")],
                        value="crepe", label="引擎(识谱算法)",
                        info="按素材选:人声/单旋律→crepe;多音/乐器和弦→basic-pitch;纯钢琴录音→bytedance。"
                             "选错会幻觉假音(如低质音频用 bytedance)。")
                    tr_key = gr.Textbox(label="调(1=主音)", value="",
                                        info="如 C=C大调;留空则自动定调(稳健,不被少数坏音带偏)")
                    tr_btn = gr.Button("扒谱", variant="primary")
            tr_report = gr.Markdown(label="报告(体检 + 可信度 + 存疑段)")
            tr_svg = gr.HTML(label="五线谱")
            tr_dl = gr.File(label="下载产物(notes/MIDI/MusicXML/简谱/可信度)", file_count="multiple")
            tr_btn.click(do_transcribe, [tr_audio, tr_engine, tr_key], [tr_report, tr_svg, tr_dl, gr.State()])

        with gr.Tab("🔊 分离(人声/伴奏/去和声/降噪)"):
            gr.Markdown("上传混音 → 三段式:BS-RoFormer 分离 → Karaoke 去和声 → UVR 降噪。**需 GPU venv**。")
            with gr.Row():
                sp_audio = gr.Audio(label="上传混音", type="filepath")
                with gr.Column():
                    sp_stages = gr.Textbox(label="处理段(按序级联)", value="1,2,3",
                                           info="1=分离人声/伴奏 · 2=去和声(去合音/backing) · 3=降噪提纯。"
                                                "填子集,如 1 / 1,2 / 1,2,3 / 2,3")
                    sp_denoise = gr.Radio(
                        [("dereverb(去混响·默认·音量稳/不闷)", "dereverb"),
                         ("deecho(去回声·备选)", "deecho")],
                        value="dereverb", label="降噪方法(第3段用)",
                        info="去混响=消房间残响(默认,更稳);去回声=消重复回声。仅在跑第3段时生效。")
                    sp_btn = gr.Button("分离", variant="primary")
            sp_out = gr.Textbox(label="日志/结果", lines=8)
            sp_dl = gr.File(label="下载各轨", file_count="multiple")
            sp_btn.click(do_separate, [sp_audio, sp_stages, sp_denoise], [sp_out, sp_dl])

        # ⭐ 三个输入的设计原因(② 决定唱成什么调 / ③ 决定听起来是谁)见
        #    docs/合并开发日志.md Entry #8 —— 前端重做前必读,别退回两输入。
        with gr.Tab("🎚️ 修音换音色"):
            gr.Markdown(
                "两段式:**修音准**(DiffPitcher)→ **换音色=本人**(Seed-VC)= 在调 + 干净 + 仍是你。**需 GPU**。\n"
                "三个输入:① 原始清唱(跑调)② 目标旋律的【去和声】干净参考 ③ 你自己的一段干净清唱(音色锚)。\n"
                "⚠️ **用法要点**:① 和 ② 要**同一段、时间对齐**;**② 必须去和声**"
                "(可先在「分离」页对原曲跑出 `clean`/`lead` 当 ②,**别直接用整首原曲**);"
                "整首会**自动分块出完整长度**(不再截断),但**时长×步数越大越慢**"
                "(整首 ×150 步在 8GB 上约 20+ 分钟;想快可先用副歌片段或把步数调低)。"
            )
            with gr.Row():
                vc_raw = gr.Audio(label="① 原始清唱(跑调)", type="filepath")
                vc_ref = gr.Audio(label="② 去和声参考(取目标旋律)", type="filepath")
                vc_self = gr.Audio(label="③ 你自己的清唱(音色锚)", type="filepath")
            with gr.Row():
                vc_csteps = gr.Slider(50, 200, value=150, step=10, label="修音步数",
                                      info="DiffPitcher 扩散迭代次数:越大音准修得越精细但越慢。默认150;想快可降到50。")
                vc_vsteps = gr.Slider(20, 100, value=50, step=10, label="换音色步数",
                                      info="Seed-VC 扩散迭代次数:越大音质越细腻但越慢。默认50;25也可用。")
                vc_cfg = gr.Slider(0.0, 1.0, value=0.7, step=0.1, label="人声近似度(cfg 引导强度)",
                                   info="越高越强调「贴目标音色」的条件(效果较细微);设 0 最快(≈1.5×)。默认0.7。"
                                        "想更像你:优先换更干净/更长的③音色锚,并适当加大换音色步数。")
            vc_btn = gr.Button("修音 + 换音色", variant="primary")
            vc_out = gr.Textbox(label="结果", lines=6)
            vc_final = gr.Audio(label="成品(在调 + 干净 + 仍是你)")
            vc_mid = gr.Audio(label="中间产物(纯修音 24k)")
            vc_btn.click(do_vocal, [vc_raw, vc_ref, vc_self, vc_csteps, vc_vsteps, vc_cfg],
                         [vc_out, vc_final, vc_mid])

        gr.Markdown("---\n小贴士:简谱图(jianpu.pdf)需本机装 LilyPond 才出图,否则只产出 `.ly`;"
                    "五线谱(Verovio)纯库无需外部程序。")
    return demo


if __name__ == "__main__":
    # 端口:环境变量优先;否则 None → gradio 自动从 7860 起找空闲端口(避免「端口被占」启动失败)。
    env_port = os.environ.get("GRADIO_SERVER_PORT")
    try:
        port = int(env_port) if env_port else None
    except ValueError:
        print(f"[MusicMaster] GRADIO_SERVER_PORT={env_port!r} 非数字,改用自动端口(从 7860 起找空闲)。")
        port = None
    print(f"\n========================================\n"
          f"  MusicMaster 启动中,请稍候(首次加载约 20-40 秒,要载入 TensorFlow)...\n"
          f"  就绪后浏览器会自动打开;若没弹出,看下方 gradio 打印的\n"
          f"  'Running on local URL' 地址(通常 http://127.0.0.1:7860,\n"
          f"   若被占用会自动顺延到 7861 等)。关闭本窗口即停止服务。\n"
          f"========================================\n", flush=True)
    build().launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=port,
        inbrowser=True,
    )
