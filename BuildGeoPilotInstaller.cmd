@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "BUILD_PS=%ROOT%packaging\build_release.ps1"

if not exist "%BUILD_PS%" goto missing_package

where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_pwsh

where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_powershell

echo 未找到 PowerShell，无法生成安装器。
pause
exit /b 1

:use_pwsh
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%BUILD_PS%" -BuildGateway -BuildInstaller
set "ERR=%ERRORLEVEL%"
pause
exit /b %ERR%

:use_powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%BUILD_PS%" -BuildGateway -BuildInstaller
set "ERR=%ERRORLEVEL%"
pause
exit /b %ERR%

:missing_package
echo 构建包不完整，缺少：%BUILD_PS%
pause
exit /b 1
