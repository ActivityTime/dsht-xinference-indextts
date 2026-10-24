# =============================================================
#  一键启动: Xinference 服务 + 加载 IndexTTS-2.5 (供 DSH-Tavern 接入)
#  用法: 双击 start_xinference_indextts.bat
#        或  pwsh -NoProfile -ExecutionPolicy Bypass -File start_xinference_indextts.ps1
#
#  说明:
#  - 必须用 127.0.0.1 而非 0.0.0.0: 0.0.0.0 会导致 xinference 内部
#    health_check 无法连接子进程而报 "Cluster is not available"。
#  - 必须调大 XINFERENCE_HEALTH_CHECK_TIMEOUT: 子进程要 ~60-90s 才就绪,
#    默认 10s 必超时。
#  - XINFERENCE_AUTH_ADVANCED=0: 关闭 4.x 新增的鉴权/管理员, 本地直连。
#  - 首次启动会自动下载 IndexTTS-2.5 权重(约6GB), 需联网并耐心等待。
# =============================================================

$ErrorActionPreference = 'Continue'

# ---- 载入中央配置 (config.ps1) ----
# 内部路径全部相对 config 定位（禁止绝对路径），外部引用在 config 明文列出。
. (Join-Path $PSScriptRoot 'config.ps1')

# ---- 可配置项（来自 config.ps1；如需改动请改 config.ps1）----
$venvDir    = $Script:VenvDir           # Xinference venv（相对定位）
$port       = $Script:Port              # 服务端口
$webHost    = $Script:WebHost           # 监听地址(务必 127.0.0.1, 勿用 0.0.0.0)
$modelName  = $Script:ModelName         # 模型名 (失败自动改试 IndexTTS2)
$smallModelsDir = ''                    # 离线部署: 填 small_models 目录; 留空则联网下载

$xin       = Join-Path $venvDir 'Scripts\xinference.exe'
$xinLocal  = Join-Path $venvDir 'Scripts\xinference-local.exe'
$endpoint  = $Script:Endpoint

# ---- 关键环境变量（config.ps1 已注入 XINFERENCE_HOME/HF_ENDPOINT/HF_HUB_DISABLE_XET/
#       XINFERENCE_HEALTH_CHECK_TIMEOUT/XINFERENCE_AUTH_ADVANCED/XINFERENCE_ENDPOINT/MODEL_NAME/
#       USE_QWEN_EMO/QWEN_DEVICE/CONSOLE_PORT/REF_AUDIO_LIB）
#      此处仅追加每次运行需要的动态项 ----

# ---- 单例互斥：禁止同时启动多个 ----
# 锁端口 = port+2 (9999)；monitor_indextts.py 用 .monitor.pid 文件锁，互不冲突。
$mutexPort = $port + 2
try {
    $mutexListener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.1'), $mutexPort)
    $mutexListener.Start()
    Write-Host "[OK] 单例锁取得 (端口 $mutexPort)"
} catch {
    Write-Host "[!] 已有启动/监控实例在运行，禁止多开 (锁端口 $mutexPort 被占用)。本脚本退出。"
    exit 0
}

function Test-Health {
    # 4.x 用 /status 探测; /health 已返回 404
    try {
        $r = Invoke-WebRequest -Uri ($endpoint + '/status') -TimeoutSec 5 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# ---------- 0) 恢复自维护 Xinference 包 (maintained\xinference-package -> venv site-packages) ----------
# Xinference 禁止下载（问题记录 #27）：本体是无外部源的自维护资产，从仓库整体恢复，不从 PyPI 安装。
# 必须在服务启动**之前**恢复：服务进程启动即 import xinference，之后同步只会影响下一次启动。
# 幂等 + 增量：逐文件 SHA256 指纹比对，一致则跳过（避免每次启动全删全拷 23MB，Defender 下能省数秒）。
function Get-PkgFingerprint($root) {
    Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\','/')
        (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash + ':' + $rel
    }
}
function Sync-Maintained {
    $srcPkg = Join-Path $PSScriptRoot 'maintained\xinference-package'
    if (-not (Test-Path -LiteralPath $srcPkg)) {
        Write-Host '[!] 自维护 Xinference 包缺失 (maintained\xinference-package 不存在)'
        return
    }
    $dstPkg = Join-Path $venvDir 'Lib\site-packages\xinference'
    if (Test-Path -LiteralPath $dstPkg) {
        $srcFp = Get-PkgFingerprint $srcPkg
        $dstFp = Get-PkgFingerprint $dstPkg
        if (($srcFp -join "`n") -eq ($dstFp -join "`n")) {
            Write-Host '[OK] 自维护 Xinference 包指纹一致，跳过同步'
            return
        }
        Remove-Item -LiteralPath $dstPkg -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $dstPkg) | Out-Null
    Copy-Item -LiteralPath $srcPkg -Destination $dstPkg -Recurse -Force
    Write-Host '[OK] 自维护 Xinference 包已恢复 (不下载)'
}
Sync-Maintained

# ---------- 1) 启动服务(若未运行) ----------
if (Test-Health) {
    Write-Host '[*] 服务已在运行: ' $endpoint
} else {
    Write-Host '[*] 启动 Xinference 服务 (首次约 60-90s, 请稍候) ...'
    Start-Process -FilePath $xinLocal -ArgumentList '--host',$webHost,'--port',$port -WindowStyle Normal
    Write-Host '[*] 等待服务就绪 (最多约 4 分钟) ...'
    $ok = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Health) { $ok = $true; break }
    }
    if (-not $ok) {
        Write-Host '[!] 服务启动超时。请检查: ' $xinLocal
        Write-Host ('     日志见 ' + (Join-Path $Script:XinferenceHome 'logs\xinference.log'))
        exit 1
    }
    Write-Host '[OK] 服务就绪: ' $endpoint
}

# ---------- 2) 加载 IndexTTS-2.5 (bf16) ----------
# 注意: xinference launch CLI 无法传 use_bf16, 必须用 Python 客户端 model_config。
# 权重本身是全精度, 此处加载时转 bf16 以减半显存(RTX3080 10GB 更稳)。
# 功能开关 use_qwen_emo/qwen_device 由 config.ps1 注入（说明文档 07）。
$env:MODEL_NAME = $modelName
$env:USE_BF16   = 'true'
if ($smallModelsDir -ne '') { $env:SMALL_MODELS_DIR = $smallModelsDir } else { Remove-Item Env:SMALL_MODELS_DIR -ErrorAction SilentlyContinue }
Write-Host '[*] 加载模型 ' $modelName ' (bf16, 首次自动下载权重约 6GB, 请耐心等待) ...'
& (Join-Path $venvDir 'Scripts\python.exe') $Script:LaunchScript

Write-Host ''
Write-Host '================ 启动完成 ================'
Write-Host 'Web UI            : ' $endpoint
Write-Host 'OpenAI 兼容端点    : ' ($endpoint + '/v1')
Write-Host '模型              : ' $modelName
Write-Host 'DSH-Tavern 配置    : Provider Endpoint -> ' ($endpoint + '/v1')
Write-Host '=========================================='

# ---------- 3.5) 独立 Web 控制台 (127.0.0.1:9998；内置 webui 已拆除，见 问题记录 #32) ----------
# 单实例：端口被占用即退出（视为已有控制台在跑）。控制台是静态页 + 代理，秒起、不占显存。
$consoleRunning = Test-NetConnection -ComputerName 127.0.0.1 -Port $Script:ConsolePort -InformationLevel Quiet -WarningAction SilentlyContinue
if ($consoleRunning) {
    Write-Host '[*] Web 控制台已在运行 (跳过): ' $Script:ConsoleUrl
} else {
    Write-Host '[*] 启动 Web 控制台 ...'
    Start-Process -FilePath (Join-Path $venvDir 'Scripts\python.exe') `
        -ArgumentList $Script:ConsoleScript -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Write-Host '[OK] Web 控制台: ' $Script:ConsoleUrl
}

# ---------- 4) 拉起守护监控（单例常驻；未运行则启动，已运行则跳过）----------
# monitor_indextts.py: 单例守卫 + 进程互监控 + 自动卸载重复实例 + 防重复加载。
# 检测 monitor 是否在运行：查 .monitor.pid 中的 PID 是否存活。
$monitorPy = Join-Path $PSScriptRoot 'scripts\monitor_indextts.py'
$monPidFile = Join-Path $PSScriptRoot '.monitor.pid'
$monRunning = $false
if (Test-Path -LiteralPath $monPidFile) {
    $oldPid = (Get-Content -LiteralPath $monPidFile -Raw).Trim()
    if ($oldPid -match '^\d+$' -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        $monRunning = $true   # PID 存活 => monitor 已在运行
    }
}
if ($monRunning) {
    Write-Host '[*] 守护监控已在运行 (跳过拉起)'
} else {
    Write-Host '[*] 拉起守护监控 (常驻, 防重复/多实例/自愈) ...'
    Start-Process -FilePath (Join-Path $venvDir 'Scripts\python.exe') -ArgumentList $monitorPy,'--interval','20' -WindowStyle Hidden
    Write-Host '[*] 守护监控已后台启动 (日志见 logs\monitor.log)'
}
