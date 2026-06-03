#!/usr/bin/env bash
# MusicMaster launcher (macOS / Linux): start the local web server and open the browser.
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "[MusicMaster] .venv not found. Run: python scripts/setup_core.py"
  exit 1
fi
export PYTHONUTF8=1 MUSICMASTER_OPEN_BROWSER=1
echo "[MusicMaster] Starting... the browser will open shortly (Ctrl+C to stop)."
exec .venv/bin/python -m musicmaster.web.server
