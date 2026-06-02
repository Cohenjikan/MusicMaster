@echo off
REM ── MusicMaster GUI 一键启动(双击本文件即可)──
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [MusicMaster] 未找到本地环境 .venv
  echo   请先在本目录运行:  python scripts\setup_core.py
  echo   建好后再双击本文件。
  echo.
  pause
  exit /b 1
)

set PYTHONUTF8=1
echo [MusicMaster] 启动中... 浏览器将打开 http://127.0.0.1:7860  (关闭本窗口即停止)
".venv\Scripts\python.exe" app.py
echo.
echo [MusicMaster] 已退出。
pause
