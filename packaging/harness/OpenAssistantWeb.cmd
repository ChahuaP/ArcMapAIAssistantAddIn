@echo off
rem ArcMap Harness console entry point (called by the ArcMap Add-in).
rem Co-located with launch_harness.ps1 inside the harness root.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_harness.ps1"
