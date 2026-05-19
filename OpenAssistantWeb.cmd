@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m gateway_py3.open_web
  exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
  python -m gateway_py3.open_web
  exit /b %errorlevel%
)

exit /b 1
