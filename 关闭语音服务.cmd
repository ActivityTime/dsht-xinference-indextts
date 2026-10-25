@echo off
title Stop Voice Service
echo ============================================
echo  Stop: Xinference service + console + watchdog + model worker
echo  Frees VRAM and ports; STATUS.txt will be marked stopped.
echo ============================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_xinference_indextts.ps1"
echo.
pause
