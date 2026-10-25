@echo off
chcp 65001 >nul 2>&1
setlocal
set "URL=http://127.0.0.1:9996/status"

echo ============================================
echo  Xinference 语音服务 - 状态页启动器
echo ============================================
echo.
echo [1/2] 检查状态服务 (9996)...
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:9996/api/health' -TimeoutSec 4 -UseBasicParsing; Write-Host '       状态服务在线' } catch { Write-Host '       状态服务无响应(可能监控未启动)' }"
echo.
echo [2/2] 正在用默认浏览器打开状态页...
powershell -NoProfile -Command "Start-Process 'http://127.0.0.1:9996/status'"
if errorlevel 1 (
  echo.
  echo [!] 浏览器未能自动打开，请手动访问:
  echo     %URL%
) else (
  echo [OK] 已请求浏览器打开，若未弹出请检查浏览器是否拦截了弹窗。
)
echo.
echo 状态页地址(可手动复制): %URL%
echo.
echo 按任意键关闭本窗口...
pause >nul
