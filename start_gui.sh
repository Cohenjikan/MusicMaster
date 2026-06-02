#!/usr/bin/env bash
# ── MusicMaster GUI 启动(macOS / Linux）──
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "[MusicMaster] 未找到 .venv,请先运行: python scripts/setup_core.py"
  exit 1
fi
export PYTHONUTF8=1
echo "[MusicMaster] 启动中... 浏览器开 http://127.0.0.1:7860 (Ctrl+C 停止)"
exec .venv/bin/python app.py
