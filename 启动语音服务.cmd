@echo off
title Start Voice Service (Xinference + IndexTTS-2.5)
echo ============================================
echo  Start voice service: Xinference + IndexTTS-2.5 model
echo  Console (9998) and watchdog monitor will start,
echo  and page shortcuts will be created in this folder.
echo ============================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_xinference_indextts.ps1"
echo.
pause
