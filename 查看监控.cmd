@echo off
title View Voice Service Monitor
echo ============================================
echo  Open the live status window (desktop indicator light).
echo  Red = abnormal, Green = normal. Click Stop to shut all down.
echo ============================================
call "%~dp0open_status.bat"
echo.
pause
