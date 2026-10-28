@echo off
rem Double-click to run (Windows). First run installs required tools (a few minutes).
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul
if errorlevel 1 (
  echo [Setup] Installing uv - Python runner...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if errorlevel 1 (
    echo Install failed. Check your internet connection.
    pause
    exit /b 1
  )
)

if not exist ".env" copy ".env.example" ".env" >nul

echo Starting... a browser window will open shortly.
echo (Closing this window stops the program and any recording. Keep it open during meetings.)
uv run --quiet streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false --server.headless false
pause
