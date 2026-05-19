@echo off
setlocal
cd /d "%~dp0"

echo Starting ArcMap AI Assistant Gateway...
echo.

if "%DEEPSEEK_API_KEY%"=="" (
  echo DEEPSEEK_API_KEY is not set in this window.
  echo If this is the first run, close this window and double-click SetupDeepSeekKey.cmd first.
  echo.
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m gateway_py3
  goto :end
)

where python >nul 2>nul
if not errorlevel 1 (
  python -m gateway_py3
  goto :end
)

echo Python 3 was not found. Use the packaged EXE build for end users.

:end
echo.
echo Gateway stopped.
pause
