@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup.cmd once before launching.
  pause
  exit /b 1
)
where node >nul 2>nul
if errorlevel 1 echo YouTube needs Node.js 22 or newer. Instagram and the offline Library can still be used.
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
