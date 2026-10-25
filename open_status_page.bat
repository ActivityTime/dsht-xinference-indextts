@echo off
REM 打开语音服务状态页（独立于控制台 9998，控制台死也能看）
REM 双击本文件（在宿主机上）即可在默认浏览器打开。
echo 正在打开状态页 http://127.0.0.1:9996/status ...
echo 若打不开：请先运行 start_xinference_indextts.ps1 启动服务与监控。
start "" "http://127.0.0.1:9996/status"
