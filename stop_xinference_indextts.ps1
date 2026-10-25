# -*- coding: utf-8 -*-
# =============================================================
#  一键关停: Xinference 服务 + 控制台 + 守护监控 + IndexTTS 模型 worker
#  用法: pwsh -NoProfile -ExecutionPolicy Bypass -File stop_xinference_indextts.ps1
#  说明:
#   - 干净关停全部组件，释放互斥端口(9999)与 .monitor.pid。
#   - 仅针对本项目的端口(9997/9998/9999)与命令行特征(xinference-local /
#     console\server.py / monitor_indextts.py / launch_indextts.py /
#     IndexTTS 模型 worker)，不误伤其他进程。
#   - 幂等: 无运行中的组件时直接报告退出。
# =============================================================

$ErrorActionPreference = 'Continue'
$Script:BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- 端口与命令行特征（无绝对路径；端口为项目固定值）----
# ⚠️ 命令行特征须足够具体：不得用裸词 xinference / Indextts —— 二者均出现在本脚本自身的
#    文件名 stop_xinference_indextts.ps1 中，会导致脚本误杀自己；亦会误伤仅“引用过路径”的
#    无关进程（如其他 AI 助手的 bash）。故只匹配本项目的可执行名/模块名。
$Ports = @(9997, 9998, 9999)
$CmdPatterns = @('xinference-local', 'xoscar\.backends', 'console\\server\.py', 'monitor_indextts\.py', 'launch_indextts\.py')

# ---- 日志 ----
$logDir = Join-Path $Script:BaseDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'stop.log'
function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

Log("[*] 开始关停 Xinference 全家桶 ...")

# 排除自身 PID（避免任何残留匹配误伤本脚本进程）
$SelfPid = $PID

# ---- 1) 按端口杀监听进程 ----
foreach ($port in $Ports) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
            if ($proc) {
                Log("[*] 端口 $port 监听进程 PID=$($proc.Id) ($($proc.ProcessName)) -> 终止")
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Log("[!] 端口 $port 查询失败: $_")
    }
}

# ---- 2) 按命令行特征杀(覆盖模型 worker 等非监听进程；子进程不随父进程级联，须显式杀) ----
try {
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $cmd = $_.CommandLine
        $cmd -and $_.ProcessId -ne $SelfPid -and ($CmdPatterns | Where-Object { $cmd -match $_ })
    }
    if ($procs) {
        foreach ($pr in $procs) {
            Log("[*] 命令行命中 PID=$($pr.ProcessId) -> 终止: $($pr.CommandLine)")
            Stop-Process -Id $pr.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } else {
        Log("[*] 无命令行特征命中的进程")
    }
} catch {
    Log("[!] 命令行匹配失败: $_")
}

# ---- 3) 释放 .monitor.pid ----
$pidFile = Join-Path $Script:BaseDir '.monitor.pid'
if (Test-Path -LiteralPath $pidFile) {
    Remove-Item -LiteralPath $pidFile -Force
    Log("[*] 已删除 .monitor.pid")
}

# ---- 4) 等待 9997 释放(最多 ~10s) ----
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    $still = Get-NetTCPConnection -LocalPort 9997 -State Listen -ErrorAction SilentlyContinue
    if (-not $still) { $ok = $true; break }
    Start-Sleep -Seconds 1
}
Log("[*] 关停完成。9997 监听: $(if ($ok) {'已释放'} else {'仍占用，请检查日志'})")
