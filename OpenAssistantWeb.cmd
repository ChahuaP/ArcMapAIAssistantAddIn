@echo off
setlocal
cd /d "%~dp0"

start "" "http://127.0.0.1:8765/?v=0.10.3-split-by-field"
exit /b 0
