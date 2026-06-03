# -*- coding: utf-8 -*-
"""四大部门的任务执行体(runner)。

每个 runner 的逻辑**精确复用** app.py 里验证过的 do_convert/do_transcribe/do_separate/do_vocal,
只是:① 返回结构化 dict(而非 Gradio 元组)② 拿 Job 句柄更新阶段 ③ 软失败(环境未就绪/无文件)
返回 {ok: False, message}, 真异常上抛由 JobManager 兜底。核心算法一字未改。

venv 路由与 app.py 一致:
  convert / transcribe  → 本进程(.venv,CPU)直接 import 调用
  separate              → 子进程到分离 venv(.venv-sep)
  vocal                 → 本进程调 vocal.pipeline.two_stage(其内部再子进程到 GPU venv)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


# ───────────────────────── 公共助手 ───────────────────────── #
def _subprocess_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _sep_python() -> Optional[str]:
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


def _classify(name: str) -> str:
    """按扩展名给产物归类(对应设计稿的文件徽标:score/midi/audio/data)。"""
    ext = Path(name).suffix.lower()
    if ext in (".musicxml", ".xml", ".mxl"):
        return "score"
    if ext in (".mid", ".midi"):
        return "midi"
    if ext in (".pdf",):
        return "midi"
    if ext in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        return "audio"
    return "data"  # .json/.svg/.txt/.jianpu/.ly/...


def _downloads(d: Path, only: Optional[list[str]] = None) -> list[dict]:
    """列出 job 目录下的产物(递归),给前端拼下载链接用。
    only:若给定文件名白名单(按序),只列其中存在的(用于控制展示顺序)。"""
    items: list[dict] = []
    if only is not None:
        for nm in only:
            p = d / nm
            if p.is_file():
                items.append(_dl_entry(d, p))
        return items
    for p in sorted(d.rglob("*")):
        if p.is_file():
            items.append(_dl_entry(d, p))
    return items


def _dl_entry(base: Path, p: Path) -> dict:
    rel = p.relative_to(base).as_posix()
    try:
        size_kb = round(p.stat().st_size / 1024, 1)
    except OSError:
        size_kb = 0.0
    return {"name": rel, "size_kb": size_kb, "kind": _classify(p.name)}


def _read_svg(path: Optional[Path]) -> Optional[str]:
    """把五线谱 SVG 文件内容内联返回(供前端注入「谱面」面板)。"""
    if path and Path(path).is_file():
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def _norm_key(key: str):
    """校验并规范化用户输入的调名。合法 → 主音大写的规范串(如 g→G、bb→Bb、F#→F#);
    无效 → None(调用方据此报错);空 → ''(交给自动定调)。music21 用 - 表示降号,故校验时转换。"""
    if not key or not key.strip():
        return ""
    s = key.strip()
    letter = s[0].upper()
    acc = s[1:].replace("♯", "#").replace("♭", "b")
    try:
        from music21 import pitch as _p
        _p.Pitch(letter + acc.replace("b", "-"))  # 解析失败抛异常 = 非法调名
        return letter + acc
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────── 互译 ─────────────────────────── #
def run_convert(job, file_path: str, direction: str, key: str = "") -> dict:
    """direction: 'j2s' 简谱→五线谱(MusicXML) | 's2j' 五线谱→简谱。"""
    if not file_path:
        return {"ok": False, "message": "请先上传文件。"}
    from musicmaster.convert import convert as cv
    d = Path(job.job_dir)
    src = Path(file_path)
    try:
        if direction == "j2s":
            job.set_stage("解析简谱 → 生成 MusicXML", 0.4)
            score = cv.load_any(src)
            if not list(score.flatten().notes):  # 0 个真实音符 = 啥都没解析出来,别谎报成功(修压测 M)
                return {"ok": False, "direction": direction, "staff_svg": None, "jianpu_text": None,
                        "downloads": [], "message": "未从输入中解析出任何音符,请检查简谱格式(参见 examples/twinkle.jianpu)。"}
            out = d / (src.stem + ".musicxml")
            score.write("musicxml", fp=str(out))
            staff_svg = None
            try:
                job.set_stage("誊写五线谱(Verovio)", 0.75)
                # core.__init__ 把 render 绑成包属性,必须从子模块直接 import 函数本身
                from musicmaster.core.render import render as render_score
                rr = render_score(str(out), str(d), formats=("staff",))
                staff_svg = _read_svg(Path(rr["staff"])) if rr.get("staff") else None
            except Exception as re:  # noqa: BLE001 — 渲染失败不该让转换失败,MusicXML 才是主产物
                print(f"[convert] 五线谱预览渲染跳过:{type(re).__name__}: {re}", flush=True)
            return {"ok": True, "message": "已译成五线谱 MusicXML。",
                    "direction": direction, "staff_svg": staff_svg, "jianpu_text": None,
                    "downloads": _downloads(d)}
        else:  # s2j
            norm_key = _norm_key(key)  # 校验+规范化调名(修压测 M:非法/小写 key 曾原样写进表头致音高矛盾)
            if key and key.strip() and norm_key is None:
                return {"ok": False, "direction": direction, "staff_svg": None, "jianpu_text": None,
                        "downloads": [], "message": f"无法识别调名「{key.strip()}」,合法如 C / G / D / A / E / B / F# / Bb / Eb 等。"}
            job.set_stage("解析五线谱 → 译回简谱", 0.5)
            score = cv.load_any(src)
            if not list(score.flatten().notes):
                return {"ok": False, "direction": direction, "staff_svg": None, "jianpu_text": None,
                        "downloads": [], "message": "未从输入中解析出任何音符,请检查文件是否为有效乐谱。"}
            text = cv.score_to_jianpu(score, key_name=(norm_key or None))
            out = d / (src.stem + ".jianpu")
            out.write_text(text, encoding="utf-8")
            return {"ok": True, "message": "已译回简谱文本。",
                    "direction": direction, "staff_svg": None, "jianpu_text": text,
                    "downloads": _downloads(d)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"转换失败:{type(e).__name__}: {e}"}


# ─────────────────────────── 记谱 ─────────────────────────── #
def run_transcribe(job, audio_path: str, engine: str = "crepe", key: str = "") -> dict:
    if not audio_path:
        return {"ok": False, "message": "请先上传清唱音频。"}
    from musicmaster.transcribe import autopilot
    _engines = getattr(autopilot, "ENGINES", ("crepe", "basic-pitch", "bytedance"))
    if engine not in _engines:  # 未知引擎会让 autopilot 的 argparse 抛 SystemExit 逃逸 except,卡死任务(修压测 M)
        return {"ok": False, "report_md": None, "staff_svg": None, "confidence_pct": None,
                "spots": None, "downloads": [],
                "message": f"未知引擎「{engine}」,可选:{', '.join(_engines)}。"}
    d = Path(job.job_dir)
    try:
        job.set_stage(f"记谱中(引擎 {engine}:体检 → 转录 → 定调 → 渲染 → 可信度)", 0.3)
        argv = [str(audio_path), "--out", str(d), "--engine", engine]
        if key and key.strip():
            argv += ["--key", key.strip()]
        rc = autopilot.main(argv)
        report = (d / "报告.md").read_text(encoding="utf-8") if (d / "报告.md").is_file() else "(无报告)"
        if rc != 0:
            report = f"> 扒谱以非零码({rc})结束,可能转录失败,详见下方报告。\n\n" + report
        staff_svg = _read_svg(d / "staff.svg")
        confidence_pct, spots = _parse_confidence(d)
        downloads = _downloads(d, only=["notes.json", "out.mid", "out.musicxml",
                                        "jianpu.pdf", "jianpu.ly", "confidence.json", "staff.svg"])
        # 若白名单一个都没命中(产物命名变化),退回全量
        if not downloads:
            downloads = _downloads(d)
        return {"ok": rc == 0, "report_md": report, "staff_svg": staff_svg,
                "confidence_pct": confidence_pct, "spots": spots, "downloads": downloads}
    except Exception as e:  # noqa: BLE001
        hint = ""
        if engine in ("basic-pitch", "bytedance"):
            hint = (f"\n\n提示:引擎「{engine}」需专用环境(basic-pitch 需 TensorFlow 2.15;"
                    f"bytedance 需 GPU)。快速上手请用默认引擎 crepe(人声/单旋律最佳)。")
        return {"ok": False, "message": f"扒谱失败:{type(e).__name__}: {str(e)[:240]}{hint}"}


def _parse_confidence(d: Path) -> tuple[Optional[int], Optional[list]]:
    """尽力从 autopilot 产物里读出整体可信度与存疑段(读不到就返回 None,前端只显示报告)。"""
    import json
    pct: Optional[int] = None
    spots: Optional[list] = None
    cf = d / "confidence.json"
    if cf.is_file():
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in ("overall", "overall_confidence", "score", "confidence"):
                    if isinstance(data.get(k), (int, float)):
                        pct = int(round(float(data[k]) * (100 if data[k] <= 1 else 1)))
                        break
                # confidence.assess() 把存疑段放在 "passages":[{start_s,end_s,n,reasons[],min_conf}]
                raw_spots = (data.get("passages") or data.get("spots")
                             or data.get("suspect") or data.get("flagged"))
                if isinstance(raw_spots, list):
                    spots = []
                    for s in raw_spots[:12]:
                        if not isinstance(s, dict):
                            continue
                        if "start_s" in s and "end_s" in s:  # assess 的 passage 结构
                            reasons = s.get("reasons") or []
                            why = "、".join(reasons) if isinstance(reasons, list) else str(reasons)
                            n, mc = s.get("n"), s.get("min_conf")
                            note = (f"{n}音 · " if n else "") + (why or "可信度偏低")
                            if isinstance(mc, (int, float)):
                                note += f"(最低可信 {mc:.2f})"
                            try:
                                at = f"{float(s['start_s']):.1f}–{float(s['end_s']):.1f}s"
                            except (TypeError, ValueError):
                                at = ""
                            spots.append({"at": at, "note": note})
                        else:
                            at = s.get("at") or s.get("time") or s.get("beat") or s.get("range") or ""
                            note = s.get("note") or s.get("reason") or s.get("desc") or ""
                            spots.append({"at": str(at), "note": str(note)})
        except Exception:  # noqa: BLE001 — 可信度解析永远是可选增强,失败不影响主流程
            pass
    return pct, spots


# ─────────────────────────── 拆声 ─────────────────────────── #
def run_separate(job, audio_path: str, stages: str = "1,2,3", denoise: str = "dereverb") -> dict:
    if not audio_path:
        return {"ok": False, "message": "请先上传混音音频。"}
    sep_py = _sep_python()
    if not sep_py:
        return {"ok": False, "message": (
            "未找到分离环境(.venv-sep)。拆声需 GPU 与 audio-separator,请按 README 用 "
            "scripts/setup_sep.py 安装,并设 paths.local.json 的 sep_python 或环境变量 "
            "MUSICMASTER_SEP_PYTHON 指向其 python。")}
    d = Path(job.job_dir)
    job.set_stage("分离中(BS-RoFormer → 去和声 → 降噪,三段级联,可能数分钟)", 0.2)
    cmd = [sep_py, "-m", "musicmaster.separate.pipeline", str(audio_path),
           "--stages", stages, "--denoise", denoise, "--out-dir", str(d)]
    try:
        r = subprocess.run(cmd, cwd=str(REPO), env=_subprocess_env(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800)
        log = (r.stdout or "") + "\n" + (r.stderr or "")
        wavs = _downloads(d)  # 分离产物都是 wav
        if r.returncode != 0:
            return {"ok": False, "message": f"分离失败(returncode={r.returncode})",
                    "log": log[-2000:], "tracks": []}
        if not wavs:
            return {"ok": False, "message": "分离子进程返回 0 但没有产出 wav,请看日志。",
                    "log": log[-2000:], "tracks": []}
        return {"ok": True, "message": f"拆好了,产物 {len(wavs)} 个。",
                "log": log[-1200:], "tracks": wavs}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "分离超时(>30 分钟),请用更短的音频或更少的处理段。",
                "log": "", "tracks": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"分离异常:{type(e).__name__}: {e}", "log": "", "tracks": []}


# ─────────────────────── 重塑(修音换音色)─────────────────────── #
def run_vocal(job, raw: str, ref: str, self_ref: str,
              correct_steps: int = 150, voice_steps: int = 50, voice_cfg: float = 0.7) -> dict:
    if not (raw and ref and self_ref):
        return {"ok": False, "message": (
            "请上传三个文件:① 原始清唱 ② 去和声参考(取目标旋律)③ 你自己的清唱(音色锚)。")}
    from musicmaster.vocal import config as vcfg, pipeline as vpipe
    probs = vcfg.check_paths("correct") + vcfg.check_paths("voice")
    if probs:
        return {"ok": False, "message": (
            "修音/换音色环境未就绪(需 GPU venv + 权重):\n  - " + "\n  - ".join(probs)
            + "\n\n见 README 的「修音换音色(GPU)」设置。")}
    d = Path(job.job_dir)
    try:
        job.set_stage("修音中 → 换音色中(整首自动分块,高精度可能 20+ 分钟)", 0.15)
        res = vpipe.two_stage(raw, ref, self_ref, d,
                              correct_steps=int(correct_steps), voice_steps=int(voice_steps),
                              voice_cfg=float(voice_cfg))
        final = res.get("final")
        detail = "\n".join(f"{k}: {v}" for k, v in res.items())
        downloads = _downloads(d)
        if final:
            return {"ok": True, "message": "两段式完成:在调 + 干净 + 仍是你。",
                    "detail": detail, "final": _relname(d, final),
                    "mid": _relname(d, res.get("corrected_24k")), "downloads": downloads}
        return {"ok": False, "message": (
            "修音完成,但换音色未产出成品(Seed-VC 没生成 vc_*.wav)。"
            "请看运行窗口日志,检查 .venv-svc 与权重是否就绪。"),
            "detail": detail, "final": None,
            "mid": _relname(d, res.get("corrected_24k")), "downloads": downloads}
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"ok": False, "message": f"失败:{type(e).__name__}: {e}",
                "detail": traceback.format_exc()[-800:]}


def _relname(base: Path, p) -> Optional[str]:
    """把绝对产物路径转成相对 job_dir 的名字(给前端拼下载/播放 URL)。"""
    if not p:
        return None
    try:
        return Path(p).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(p).name
