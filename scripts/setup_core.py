#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建主环境(CPU):GUI + 扒谱 + 互转 + 渲染。

    python scripts/setup_core.py [--venv .venv]

做的事:建 venv → 装 TensorFlow → `--no-deps` 装 crepe(避免降级 TF)→ 装 requirements-core → 可编辑安装本包。
"""
from __future__ import annotations
import argparse, os, subprocess, sys, venv
from pathlib import Path

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
    run("install", "tensorflow>=2.15,<2.16")
    print("[4/5] 装 crepe(--no-deps,保 TF 不被降级)")
    run("install", "--no-deps", "crepe")
    print("[5/5] 装 requirements-core + 本包")
    run("install", "-r", str(ROOT / "requirements-core.txt"))
    run("install", "-e", str(ROOT))

    print("\n✅ 完成。启动 GUI:")
    print(f"    {py} {ROOT / 'app.py'}")
    print("简谱出图还需 LilyPond,并设 LILYPOND_EXE(见 README)。")


if __name__ == "__main__":
    main()
