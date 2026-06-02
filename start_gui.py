#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""稳妥启动器(.bat 双击失败时用这个)。

双击本文件 → 走系统 Python(只用标准库)→ 自动转交本地 .venv 的 python 跑 app.py。
也可命令行:python start_gui.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
vpy = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
app = ROOT / "app.py"

if not vpy.exists():
    print("[MusicMaster] 未找到 .venv,请先运行: python scripts/setup_core.py")
    input("按回车退出...")
    sys.exit(1)

env = dict(os.environ)
env["PYTHONUTF8"] = "1"
print("[MusicMaster] 启动中... 首次加载约 20-40 秒(载入 TensorFlow),浏览器会自动打开 http://127.0.0.1:7860")
print("[MusicMaster] 保持本窗口 = 服务运行;关闭窗口 / Ctrl+C 停止。")
try:
    rc = subprocess.call([str(vpy), str(app)], env=env)
except KeyboardInterrupt:
    rc = 0
input("\n[MusicMaster] 已退出(code %s)。按回车关闭..." % rc)
