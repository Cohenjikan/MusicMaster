"""vocal 子进程包装的路径解析(环境变量可配 + 合理默认)。

修音(DiffPitcher)与换音色(Seed-VC)是 GPU 重依赖、且各自有独立 venv 与权重,
不放进主进程,而是以子进程调用各自 venv 中验证过的脚本。本模块集中解析:

  ┌ 环境变量(优先)──────────────────┬ 默认(开源 setup 后)──────────────┐
  │ MUSICMASTER_DIFFPITCHER_DIR        │ <repo>/vendor/DiffPitcher          │
  │ MUSICMASTER_VOCAL_PYTHON           │ <DiffPitcher>/../.venv-neural/...   │
  │ MUSICMASTER_SEEDVC_DIR             │ <repo>/vendor/seed-vc              │
  │ MUSICMASTER_SVC_PYTHON             │ <seed-vc>/../.venv-svc/...          │
  └────────────────────────────────────┴────────────────────────────────────┘

本机验证可直接把前两/后两个环境变量指向现有 `singer/neural/DiffPitcher` 与
`singer/neural/seed-vc`(及其 .venv-neural / .venv-svc),复用已下载的权重。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import _paths

# musicmaster 仓库根(本文件在 <repo>/musicmaster/vocal/config.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_under(venv_dir: Path) -> Path:
    """返回 venv 目录下的 python 可执行文件(跨平台)。"""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def diffpitcher_dir() -> Path:
    return Path(_paths.resolve("diffpitcher_dir", "MUSICMASTER_DIFFPITCHER_DIR",
                               str(_REPO_ROOT / "vendor" / "DiffPitcher")))


def vocal_python() -> Path:
    p = _paths.resolve("vocal_python", "MUSICMASTER_VOCAL_PYTHON", None)
    if p:
        return Path(p)
    return _python_under(diffpitcher_dir().parent / ".venv-neural")


def seedvc_dir() -> Path:
    return Path(_paths.resolve("seedvc_dir", "MUSICMASTER_SEEDVC_DIR",
                               str(_REPO_ROOT / "vendor" / "seed-vc")))


def svc_python() -> Path:
    p = _paths.resolve("svc_python", "MUSICMASTER_SVC_PYTHON", None)
    if p:
        return Path(p)
    return _python_under(seedvc_dir().parent / ".venv-svc")


def subprocess_env() -> dict:
    """子进程环境:强制 UTF-8(Windows 控制台默认 GBK,否则中文/♯ 崩 UnicodeEncodeError)。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def check_paths(kind: str) -> list[str]:
    """返回缺失项的人话提示列表(空 = 一切就绪)。kind ∈ {'correct','voice'}。"""
    problems: list[str] = []
    if kind == "correct":
        dp = diffpitcher_dir()
        py = vocal_python()
        if not (dp / "run_qt4.py").is_file():
            problems.append(
                f"未找到 DiffPitcher(缺 {dp/'run_qt4.py'})。"
                f"请 `python scripts/setup_vocal.py` 或设 MUSICMASTER_DIFFPITCHER_DIR 指向已有 DiffPitcher。"
            )
        if not py.is_file():
            problems.append(
                f"未找到修音 venv 的 python(缺 {py})。设 MUSICMASTER_VOCAL_PYTHON 指向 .venv-neural 的 python。"
            )
        for _w in ("ckpts/world_fixed_40.pt", "ckpts/bigvgan_24khz_100band/g_05000000.pt"):
            if not (dp / _w).is_file():
                problems.append(
                    f"未找到 DiffPitcher 权重(缺 {dp / _w})。请把 ckpts 放到 {dp}/ckpts/"
                    f"(见 scripts/setup_vocal.py 的「权重」提示),否则修音会在子进程崩在 torch.load。"
                )
    elif kind == "voice":
        sv = seedvc_dir()
        py = svc_python()
        if not (sv / "inference.py").is_file():
            problems.append(
                f"未找到 Seed-VC(缺 {sv/'inference.py'})。"
                f"请 `python scripts/setup_vocal.py` 或设 MUSICMASTER_SEEDVC_DIR 指向已有 seed-vc。"
            )
        if not py.is_file():
            problems.append(
                f"未找到换音色 venv 的 python(缺 {py})。设 MUSICMASTER_SVC_PYTHON 指向 .venv-svc 的 python。"
            )
    return problems
