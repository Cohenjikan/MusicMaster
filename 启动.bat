@echo off
rem MusicMaster launcher. Double-click to start the local web UI and open the browser.
rem NOTE: keep this file PURE ASCII -- cmd parses .bat in the system (GBK) codepage,
rem so any non-ASCII char here would be mojibake and break command parsing.
chcp 65001 >nul
cd /d "%~dp0"
set "MUSICMASTER_OPEN_BROWSER=1"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo [MusicMaster] Virtual env .venv not found.
  echo   Please run setup once:  python scripts\setup_core.py
  echo.
  pause
  exit /b 1
)
echo [MusicMaster] Starting... the browser will open at http://127.0.0.1:7860
echo   (Close this window to stop the server.)
"%PY%" -m musicmaster.web.server
echo.
echo [MusicMaster] Server stopped.
pause
