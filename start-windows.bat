@echo off
rem Double-click to run (Windows). First run installs required tools (a few minutes).
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
set "PYTHONUTF8=1"

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

echo Preparing... The first run downloads required files and can take 2-5 minutes.
uv run python launch.py
pause
