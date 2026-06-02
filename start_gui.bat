@echo off
REM ── MusicMaster GUI 一键启动(双击本文件即可)──
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [MusicMaster] 未找到本地环境 .venv
  echo   请先在本目录用命令行运行:  python scripts\setup_core.py
  echo   建好后再双击本文件。
  echo.
  pause
  exit /b 1
)

set PYTHONUTF8=1
echo.
echo [MusicMaster] 启动中... 首次加载约 20-40 秒(要载入 TensorFlow)。
echo [MusicMaster] 就绪后浏览器会自动打开;若没弹出,手动访问 http://127.0.0.1:7860
echo [MusicMaster] 本窗口保持打开 = 服务运行中;关闭窗口即停止。
echo.

".venv\Scripts\python.exe" app.py
set EC=%errorlevel%

echo.
echo ============================================================
echo [MusicMaster] 服务已退出(exit code = %EC%)。
echo 若是启动失败:请把上方的报错文字(整段)截图或复制发给我。
echo 也可手动诊断:在本目录命令行运行
echo     .venv\Scripts\python.exe app.py
echo ============================================================
pause
