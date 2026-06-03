@echo off
rem MusicMaster 启动器 —— 双击即可。启动本地 Web 服务并自动打开浏览器。
chcp 65001 >nul
cd /d "%~dp0"
set MUSICMASTER_OPEN_BROWSER=1
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo [MusicMaster] 未找到虚拟环境 .venv
  echo   请先建环境(一次^): python scripts\setup_core.py
  echo.
  pause
  exit /b 1
)
"%PY%" -m musicmaster.web.server
echo.
echo [MusicMaster] 服务已停止。
pause
