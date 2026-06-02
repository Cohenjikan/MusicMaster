#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MusicMaster 顶层 CLI:`musicmaster <子命令> ...`。

子命令(各自转发到对应模块,重依赖按需懒加载):
  gui          启动 Gradio Web GUI(= python app.py)
  transcribe   扒谱(autopilot:质量门→引擎→定调→渲染→可信度)
  convert      简谱 ⇄ 五线谱 互转
  render       MusicXML → 五线谱 + 简谱
  separate     三段式人声分离(需 GPU venv)
  vocal        修音 + 换音色 两段式(需 GPU venv)
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

_HELP = __doc__


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_HELP)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "gui":
        import os
        import subprocess
        from pathlib import Path

        app = Path(__file__).resolve().parents[1] / "app.py"
        return subprocess.call([sys.executable, str(app), *rest], env={**os.environ, "PYTHONUTF8": "1"})

    if cmd == "transcribe":
        from .transcribe import autopilot
        return autopilot.main(rest)
    if cmd == "convert":
        from .convert import convert
        return convert.main(rest)
    if cmd == "render":
        from .core import cli as core_cli
        return core_cli.main(["render", *rest])
    if cmd == "separate":
        from .separate import pipeline
        return pipeline.main(rest)
    if cmd == "vocal":
        from .vocal import pipeline as vpipe
        return vpipe.main(rest) if hasattr(vpipe, "main") else _vocal_via_module(rest)

    print(f"未知子命令: {cmd}\n")
    print(_HELP)
    return 2


def _vocal_via_module(rest):
    import runpy
    sys.argv = ["musicmaster.vocal.pipeline", *rest]
    runpy.run_module("musicmaster.vocal.pipeline", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
