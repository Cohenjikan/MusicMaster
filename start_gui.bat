@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto NOVENV
set PYTHONUTF8=1
echo [MusicMaster] Starting... first load takes 20-40s (loading TensorFlow).
echo [MusicMaster] A browser will open http://127.0.0.1:7860 automatically.
echo [MusicMaster] Keep this window OPEN = server running. Close it to stop.
echo.
".venv\Scripts\python.exe" app.py
echo.
echo [MusicMaster] Server exited (code %errorlevel%). If launch failed, screenshot the lines above.
pause
exit /b 0
:NOVENV
echo [MusicMaster] .venv not found. Run first:  python scripts\setup_core.py
pause
exit /b 1
