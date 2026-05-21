@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "INSTALL_PS=%ROOT%packaging\install.ps1"

if not exist "%INSTALL_PS%" goto missing_package

net session >nul 2>nul
if %ERRORLEVEL% NEQ 0 goto elevate

where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_pwsh

where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_powershell

echo 未找到 PowerShell，无法安装。
pause
exit /b 1

:use_pwsh
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS%"
set "ERR=%ERRORLEVEL%"
pause
exit /b %ERR%

:use_powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS%"
set "ERR=%ERRORLEVEL%"
pause
exit /b %ERR%

:missing_package
echo 安装包不完整，缺少：%INSTALL_PS%
pause
exit /b 1

:elevate
echo 正在请求管理员权限...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b 0
