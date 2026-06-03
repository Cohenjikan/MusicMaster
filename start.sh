#!/usr/bin/env bash
# ── MusicMaster 启动(macOS / Linux)── 启动本地 Web 服务并自动打开浏览器。
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "[MusicMaster] 未找到 .venv,请先运行: python scripts/setup_core.py"
  exit 1
fi
export PYTHONUTF8=1 MUSICMASTER_OPEN_BROWSER=1
echo "[MusicMaster] 启动中... 就绪后浏览器自动打开 (Ctrl+C 停止)"
exec .venv/bin/python -m musicmaster.web.server
