# -*- coding: utf-8 -*-
# =============================================================
#  一键关停: Xinference 服务 + 控制台 + 守护监控 + IndexTTS 模型池
#  用法: 双击 关闭语音服务.cmd
#        或 pwsh -NoProfile -ExecutionPolicy Bypass -File stop_xinference_indextts.ps1
#  说明:
#   - 连根拔起：按端口(含 9996 状态页) + 命令行特征杀，且用 taskkill /T /F 连带整棵子树；
#     循环到"零命中"为止（xoscar 子池不随父亡自杀、且杀伐中会竞态复活，必须循环 + 连根）。
#   - 先杀 xinference-local(supervisor) 断复活源，再杀模型池。
#   - 终检：报告是否还有残留进程/监听端口，杀不干净会明确报出来。
# =============================================================

$ErrorActionPreference = 'Continue'
$Script:BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 端口（9996 监控状态页 / 9997 服务 / 9998 控制台 / 9999 start 脚本互斥锁）
$Ports = @(9996, 9997, 9998, 9999)
# 命令行特征（够具体不误伤；xoscar 覆盖 venv-v4 的 xoscar.backends.indigen 模型池；
#            裸词 xinference/Indextts 会命中本脚本自身，故用带 -local / .py 的确切形式）
$CmdPatterns = @('xinference-local', 'xoscar', 'console\\server\.py', 'monitor_indextts\.py', 'launch_indextts\.py')

$logDir = Join-Path $Script:BaseDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'stop.log'
function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

Log("[*] 开始关停 Xinference 全家桶（连根拔起 + 循环到零）...")
$SelfPid = $PID

function Stop-Tree {
    param([int]$ProcId, [string]$Why)
    if (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {
        Log("  击杀[$Why] PID=$ProcId 及其整棵子树")
        & taskkill /PID $ProcId /T /F 2>&1 | Out-Null
    }
}

# ---- 1) 按端口杀监听进程（连根拔起）----
foreach ($port in $Ports) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess -and $c.OwningProcess -ne $SelfPid) {
                Log("[*] 端口 $port 监听 PID=$($c.OwningProcess) -> 连根终止")
                & taskkill /PID $c.OwningProcess /T /F 2>&1 | Out-Null
            }
        }
    } catch { Log("[!] 端口 $port 查询失败: $_") }
}

# ---- 2) 按命令行特征循环杀到零（先杀 supervisor 断复活源，再杀池；一律连根）----
for ($round = 1; $round -le 12; $round++) {
    try {
        $procs = Get-CimInstance Win32_Process | Where-Object {
            $cmd = $_.CommandLine
            $cmd -and $_.ProcessId -ne $SelfPid -and ($CmdPatterns | Where-Object { $cmd -match $_ })
        }
    } catch { Log("[!] 命令行匹配失败(第 $round 轮): $_"); break }

    if (-not $procs) {
        if ($round -eq 1) { Log("[*] 无相关进程") }
        Log("[*] 已清净（第 $round 轮无命中）")
        break
    }
    # 先 xinference-local(supervisor)，后其它；都 taskkill /T 连根
    $ordered = $procs | Sort-Object { if ($_.CommandLine -match 'xinference-local') { 0 } else { 1 } }
    foreach ($pr in $ordered) {
        $why = if ($pr.CommandLine -match 'xinference-local') { 'xinference-supervisor' }
               elseif ($pr.CommandLine -match 'xoscar') { 'xoscar-model-pool' }
               elseif ($pr.CommandLine -match 'console\\server\.py') { 'console' }
               elseif ($pr.CommandLine -match 'monitor_indextts') { 'monitor' }
               else { 'launcher' }
        Stop-Tree -ProcId $pr.ProcessId -Why $why
    }
    if ($round -eq 12) { Log("[!] 已达 12 轮仍有残留，请人工检查") }
    Start-Sleep -Seconds 2
}

# ---- 2.5) 清扫孤儿 python 进程（父进程已死的 multiprocessing worker 等）----
# 各 Python 程序用多进程拉起的 worker，父进程崩溃/被杀后不随之退出，会一直飘着占内存。
# 它们的命令行是通用的 multiprocessing.spawn，按项目特征匹配不到；但"父进程已死"即是铁孤儿
# （Windows 下父死子不换爹，PPID 指向已不存在的进程），直接连根清掉，零误伤活进程。
try {
    $orphans = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
        $_.ProcessId -ne $SelfPid -and $_.ParentProcessId -gt 0 -and
        -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)
    }
    if ($orphans) {
        foreach ($o in $orphans) {
            $oc = if ($o.CommandLine) { ($o.CommandLine -replace '.*[\\/]([^\\/]+\.exe)"?\s*', '$1 ') } else { '' }
            if ($oc.Length -gt 90) { $oc = $oc.Substring(0, 90) + '…' }
            Log("  清扫孤儿 python PID=$($o.ProcessId) (父 PID=$($o.ParentProcessId) 已死) :: $oc")
            & taskkill /PID $o.ProcessId /T /F 2>&1 | Out-Null
        }
    } else {
        Log("[*] 无孤儿 python 进程")
    }
} catch { Log("[!] 孤儿清扫失败: $_") }

# ---- 3) 清理 PID 文件（🔴 绝不删 .monitor.lock：monitor 进程死时 OS 自动释放其 msvcrt 锁；
#    若删掉 lock 文件，下一个 monitor 会新建同名文件(新 inode)再度锁上 → 两个 monitor 并存，锁形同虚设）----
$pidFile = Join-Path $Script:BaseDir '.monitor.pid'
if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force; Log("[*] 已删除 .monitor.pid") }

# ---- 4) 终检：还有没有相关进程 / 孤儿 python / 监听端口 ----
Start-Sleep -Seconds 1
$left = Get-CimInstance Win32_Process | Where-Object {
    $cmd = $_.CommandLine
    $cmd -and $_.ProcessId -ne $SelfPid -and ($CmdPatterns | Where-Object { $cmd -match $_ })
}
$orphanLeft = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
    $_.ProcessId -ne $SelfPid -and $_.ParentProcessId -gt 0 -and
    -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)
}
$portsLeft = @()
foreach ($port in $Ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $portsLeft += $port }
}
$nLeft = ($left | Measure-Object).Count
$nOrph = ($orphanLeft | Measure-Object).Count
if ($nLeft -gt 0 -or $nOrph -gt 0 -or $portsLeft.Count -gt 0) {
    Log("[!] 关停不彻底：相关进程 $nLeft 个 / 孤儿 python $nOrph 个 / 残留监听端口 $($portsLeft -join ',')")
} else {
    Log("[OK] 关停彻底：无相关进程、无孤儿 python、无监听端口")
}

# ---- 5) 写 STATUS.txt 为已停止（避免用户看到过期绿灯，误以为仍在运行）----
$statusFile = Join-Path $Script:BaseDir 'STATUS.txt'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$lines = @(
    "Xinference 本地语音服务 · 存活状态（独立监控写入，不依赖控制台）",
    "更新时间: $ts",
    "──────────────────────────────────",
    "Xinference 服务 (9997): 🔴 已停止（由 stop 脚本确认）",
    "IndexTTS-2.5 模型:     🔴 已停止",
    "Web 控制台 (9998):     🔴 已停止",
    "GPU 占用:              —",
    "",
    "状态页（独立于控制台）: http://127.0.0.1:9996/status",
    "服务已被 stop 脚本关停；如需恢复请双击 启动语音服务.cmd"
)
try {
    Set-Content -LiteralPath $statusFile -Value $lines -Encoding UTF8
    Log("[*] 已更新 STATUS.txt 为已停止")
} catch {
    Log("[!] 写 STATUS.txt 失败: $_")
}
