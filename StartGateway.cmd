@echo off
setlocal
cd /d "%~dp0"

echo Starting ArcMap AI Assistant Gateway...
echo.

if exist "%~dp0gateway\ArcMapAIAssistantGateway.exe" (
  start "" "%~dp0gateway\ArcMapAIAssistantGateway.exe"
  exit /b 0
)

if exist "%~dp0ArcMapAIAssistantGateway.exe" (
  start "" "%~dp0ArcMapAIAssistantGateway.exe"
  exit /b 0
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
