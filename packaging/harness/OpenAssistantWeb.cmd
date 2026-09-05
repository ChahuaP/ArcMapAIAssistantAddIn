@echo off
rem ArcMap Harness console entry point (called by the ArcMap Add-in).
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0harness\launch_harness.ps1"
