@echo off
setlocal
cd /d "%~dp0"

where pwsh.exe >nul 2>nul
if not errorlevel 1 (
  pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\uninstall.ps1"
  pause
  exit /b %errorlevel%
)

where powershell.exe >nul 2>nul
if not errorlevel 1 (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\uninstall.ps1"
  pause
  exit /b %errorlevel%
)

echo 未找到 PowerShell，无法卸载。
pause
exit /b 1
