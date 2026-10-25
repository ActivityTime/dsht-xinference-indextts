@echo off
title Start Watchdog Monitor (monitor_indextts)
echo ============================================================
echo  Start ONLY the watchdog monitor (not the whole stack).
echo  Polls 9997 service / 9998 console / model every 20s and
echo  auto-restarts whatever goes down. Status page on 9996.
echo  If the whole stack is down, use the Chinese Start-Voice-Service .cmd instead.
echo ============================================================
cd /d "%~dp0"

REM --- 防重复：9996 已被监听 = 监控已在跑，绝不拉第二个 ---
REM （监控自身还有 .monitor.lock msvcrt 锁兜底：重复起会被锁挡掉并自动退出）
netstat -ano | findstr "127.0.0.1:9996 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [!] Watchdog monitor already running (port 9996 in use). Not starting a second one.
    echo     Status page: http://127.0.0.1:9996/status
) else (
    start "" /min "venv\Scripts\python.exe" "scripts\monitor_indextts.py" --interval 20
    echo [OK] Watchdog monitor launched (log: logs\monitor.log; status: http://127.0.0.1:9996/status)
)
echo.
pause
