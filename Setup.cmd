@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1"
if errorlevel 1 (
  echo Setup failed. Review the message above and see README.md.
  pause
  exit /b 1
)
echo Setup complete. Double-click Launch.cmd to open Instagram Local.
pause
