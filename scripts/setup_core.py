#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建主环境(CPU):GUI + 扒谱 + 互转 + 渲染。

    python scripts/setup_core.py [--venv .venv]

做的事:建 venv → 装 TensorFlow → `--no-deps` 装 crepe(避免降级 TF)→ 装 requirements-core → 可编辑安装本包。
"""
from __future__ import annotations
import argparse, os, subprocess, sys, venv
from pathlib import Path

# 本脚本用【系统 Python】在默认 GBK 控制台运行;输出/重定向遇非 GBK 字符会 UnicodeEncodeError,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]


def venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=str(ROOT / ".venv"))
    a = ap.parse_args()
    vdir = Path(a.venv)

    print(f"[1/5] 建虚拟环境 {vdir}")
    venv.create(vdir, with_pip=True)
    py = str(venv_python(vdir))

    def run(*pip_args):
        subprocess.check_call([py, "-m", "pip", *pip_args])

    print("[2/5] 升级 pip")
    run("install", "-U", "pip")
    print("[3/5] 装 TensorFlow(CREPE/basic-pitch 后端)")
    run("install", "tensorflow==2.15.0")  # 固定 2.15.0:唯一同时满足 basic-pitch(<2.15.1)与 crepe 的版本
    print("[4/5] 装 crepe(--no-deps,保 TF 不被降级)")
    run("install", "--no-deps", "crepe")
    print("[5/5] 装 requirements-core + 本包")
    run("install", "-r", str(ROOT / "requirements" / "requirements-core.txt"))
    run("install", "-e", str(ROOT))

    print("\n[OK] 完成。启动 GUI:双击 启动.bat")
    print(f"    (或命令行: {py} -m musicmaster.web.server)")
    print("简谱出图还需 LilyPond,并设 LILYPOND_EXE(见 README)。")


if __name__ == "__main__":
    main()
