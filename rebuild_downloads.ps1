# =============================================================
#  一键重建 downloads/ 可下载区（权重 + 两个 venv）
#  用法:
#    pwsh -NoProfile -ExecutionPolicy Bypass -File rebuild_downloads.ps1            # 重建全部
#    pwsh -NoProfile -ExecutionPolicy Bypass -File rebuild_downloads.ps1 -Target weights
#    pwsh -NoProfile -ExecutionPolicy Bypass -File rebuild_downloads.ps1 -Target venv-main -Force
#  说明:
#    - 可下载区 (downloads/) 全部可由可靠源一键重建:
#       权重   -> HuggingFace IndexTeam/IndexTTS-2.5
#       venv   -> pip (torch 系列走 PyTorch cu128 官方索引 download.pytorch.org)
#    - 自维护区 (maintained/) 不在此脚本范围: 自维护 Xinference 包(Sync-Maintained)、
#      scripts、config、文档、git 均由 git 维护（Xinference 禁止下载，从仓库恢复）。
#    - 重建后自动: 恢复 junction(venv/.xinference 指向)、恢复自维护 Xinference 包。
#    - 重建前会自动停止全部 Xinference 进程(venv 被占用时无法删除/重建)。
#  前提: base Python 3.13 (见 config.ps1 ExternalDeps)；联网(权重/依赖下载)。
# =============================================================
param(
    [ValidateSet('all','weights','venv-main','venv-v4')]
    [string]$Target = 'all',
    [switch]$Force
)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'config.ps1')

# ---- 路径（全部相对 config 定位；外部依赖从 config ExternalDeps 读）----
$root        = $Script:BaseDir
$downloads   = Join-Path $root 'downloads'
$modelsDir   = Join-Path $downloads 'models\IndexTTS-2.5'
$venvMainDir = Join-Path $downloads 'venv-main'
$venvV4Dir   = Join-Path $downloads 'venv-v4\IndexTTS-2.5\default\3.13.15'
$reqMain     = Join-Path $root 'maintained\requirements-venv-main.txt'
$reqV4       = Join-Path $root 'maintained\requirements-venv-v4.txt'
$basePy      = Join-Path $Script:ExternalDeps['BasePython313 (venv base_prefix, runtime)'] 'python.exe'
$hfRepo      = 'IndexTeam/IndexTTS-2.5'
$torchIndex  = 'https://download.pytorch.org/whl/cu128'
# PyTorch cu128 wheel（下载到 downloads\ 根，与 requirements 记录的 file:// 路径一致）
$torchWheels = @(
    'torch-2.11.0%2Bcu128-cp313-cp313-win_amd64.whl',
    'torchaudio-2.11.0%2Bcu128-cp313-cp313-win_amd64.whl',
    'torchvision-0.26.0%2Bcu128-cp313-cp313-win_amd64.whl'
)

function Write-Step([string]$msg) { Write-Host "[rebuild] $msg" -ForegroundColor Cyan }

# ---------- 停止全部 Xinference 进程（venv 占用无法删除/重建） ----------
function Stop-XinferenceProcesses {
    Write-Step '停止全部 Xinference 进程 ...'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'xinference' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 3
    $left = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'xinference' }
    if ($left) { Write-Step "[!] 仍有 Xinference 进程未停: $($left.ProcessId -join ',')" } else { Write-Step 'OK 已全部停止' }
}

# ---------- 下载 PyTorch cu128 wheel 到 downloads\ 根（存在则跳过） ----------
function Get-TorchWheels {
    Write-Step "下载 PyTorch cu128 wheel 到 downloads\ (源: $torchIndex) ..."
    foreach ($whl in $torchWheels) {
        $name = $whl.Replace('%2B', '+')
        $dst  = Join-Path $downloads $name
        if (Test-Path -LiteralPath $dst) { Write-Step "  已存在跳过: $name"; continue }
        $url = "$torchIndex/$whl"
        Write-Step "  下载 $name (可能较大, 请耐心) ..."
        curl.exe -L --retry 3 -o $dst $url 2>&1 | Out-Null
        if (-not (Test-Path -LiteralPath $dst)) {
            Write-Step "[!] 下载失败: $url"
        } else {
            $sz = '{0:N0}MB' -f ((Get-Item -LiteralPath $dst).Length / 1MB)
            Write-Step "  OK $name ($sz)"
        }
    }
}

# ---------- 新建 pip venv + 安装依赖 ----------
function New-PipVenv {
    param([string]$VenvDir, [string]$Requirements, [switch]$SystemSite)
    Write-Step "新建 venv: $VenvDir"
    if (Test-Path -LiteralPath $VenvDir) {
        if ($Force) { Remove-Item -LiteralPath $VenvDir -Recurse -Force }
        else { Write-Step "[!] $VenvDir 已存在，跳过(用 -Force 强制重建)"; return $false }
    }
    $args = @('-m', 'venv')
    if ($SystemSite) { $args += '--system-site-packages' }
    $args += $VenvDir
    & $basePy @args
    if ($LASTEXITCODE -ne 0) { Write-Step "[!] venv 创建失败"; return $false }
    $vpy = Join-Path $VenvDir 'Scripts\python.exe'
    # torch 三件套: 用已下载的本地 cu128 wheel（requirements 记录即 file:// 指向 downloads\ 根）
    Write-Step '  安装 torch/torchaudio/torchvision (cu128 本地 wheel) ...'
    & $vpy -m pip install --no-index --find-links $downloads torch torchaudio torchvision 2>&1 | Select-Object -Last 1
    # 其余依赖: pip install -r（torch 三件套命中本地 wheel，其余走 pip 源）
    Write-Step "  安装其余依赖 (-r $(Split-Path $Requirements -Leaf)) ..."
    & $vpy -m pip install -r $Requirements 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Step "[!] 依赖安装未完全成功，请检查输出" }
    else { Write-Step '  OK 依赖安装完成' }
    return $true
}

# ---------- 权重: HuggingFace IndexTeam/IndexTTS-2.5 ----------
function Rebuild-Weights {
    if (Test-Path -LiteralPath $modelsDir) {
        if (-not $Force) { Write-Step "[!] 权重已存在 ($modelsDir)，跳过(用 -Force 强制重下)"; return }
        Write-Step '删除旧权重目录 ...'
        Remove-Item -LiteralPath $modelsDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
    Write-Step "从 HuggingFace 下载 $hfRepo -> $modelsDir (约 10.5GB, 请耐心) ..."
    $env:HF_HUB_OFFLINE = '0'          # 重建必须联网
    $env:HF_ENDPOINT    = 'https://hf-mirror.com'
    $vpy = Join-Path $venvMainDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $vpy)) { $vpy = $basePy }   # venv 缺失时用 base(需含 huggingface_hub)
    & $vpy -c "from huggingface_hub import snapshot_download; snapshot_download('$hfRepo', local_dir=r'$modelsDir')" 2>&1 | Select-Object -Last 5
    if (Test-Path -LiteralPath (Join-Path $modelsDir 'gpt.pth')) { Write-Step 'OK 权重下载完成 (gpt.pth 存在)' }
    else { Write-Step '[!] 权重下载未完成，请检查网络/HF 可达性' }
    # 提示: hf_cache 组件(bigvgan/w2v-bert/campplus/semantic_codec)由 IndexTTS 首次运行自动补齐
    Write-Step '[提示] hf_cache 组件由 IndexTTS 首次运行自动下载，首次启动需联网并临时关闭 HF_HUB_OFFLINE'
}

# ---------- 主 venv (Xinference 框架) ----------
function Rebuild-VenvMain {
    Write-Step '==== 重建主 venv (venv-main) ===='
    $ok = New-PipVenv -VenvDir $venvMainDir -Requirements $reqMain
    if ($ok) { Sync-Maintained; Write-Step 'OK 主 venv 重建完成(含自维护覆盖)' }
}

# ---------- v4 模型环境 (IndexTTS 推理) ----------
function Rebuild-VenvV4 {
    Write-Step '==== 重建 v4 模型环境 (venv-v4) ===='
    $ok = New-PipVenv -VenvDir $venvV4Dir -Requirements $reqV4 -SystemSite
    if ($ok) { Write-Step 'OK v4 环境重建完成' }
}

# ---------- 恢复自维护 Xinference 包: maintained\xinference-package -> venv site-packages ----------
# Xinference 禁止下载（用户要求 #27）：本体是自维护资产（无外部源），从仓库恢复，不从 PyPI 安装。
function Sync-Maintained {
    $srcPkg = Join-Path $root 'maintained\xinference-package'
    if (-not (Test-Path -LiteralPath $srcPkg)) { Write-Step '[!] maintained\xinference-package 缺失，无法恢复 Xinference'; return }
    $dstPkg = Join-Path $venvMainDir 'Lib\site-packages\xinference'
    if (Test-Path -LiteralPath $dstPkg) { Remove-Item -LiteralPath $dstPkg -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $dstPkg) | Out-Null
    Copy-Item -LiteralPath $srcPkg -Destination $dstPkg -Recurse -Force
    Write-Step 'OK 自维护 Xinference 包已恢复 (不下载)'
}

# ---------- 确保 junction（venv / cache / virtualenv 指向 downloads） ----------
function Ensure-Junctions {
    $targets = @(
        @{ Link = (Join-Path $root 'venv');                              Target = (Join-Path $downloads 'venv-main') },
        @{ Link = (Join-Path $root '.xinference\cache\v2\IndexTTS-2_5'); Target = (Join-Path $downloads 'models\IndexTTS-2.5') },
        @{ Link = (Join-Path $root '.xinference\virtualenv\v4');         Target = (Join-Path $downloads 'venv-v4') }
    )
    foreach ($t in $targets) {
        $item = Get-Item -LiteralPath $t.Link -Force -ErrorAction SilentlyContinue
        if ($item -and $item.LinkType -eq 'Junction' -and $item.Target -eq $t.Target) { continue }
        if ($item) { Remove-Item -LiteralPath $t.Link -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Force -Path (Split-Path $t.Link) | Out-Null
        New-Item -ItemType Junction -Path $t.Link -Target $t.Target | Out-Null
        Write-Step "junction 已恢复: $($t.Link) -> $($t.Target)"
    }
    Write-Step 'OK 所有 junction 正常'
}

# ============================ main ============================
Write-Step "重建目标: $Target  (Force=$Force)"
if ($Target -in @('all','venv-main','venv-v4')) { Stop-XinferenceProcesses }
if ($Target -in @('all','venv-main','venv-v4')) { Get-TorchWheels }

if ($Target -in @('all','venv-main')) { Rebuild-VenvMain }
if ($Target -in @('all','venv-v4'))   { Rebuild-VenvV4 }
if ($Target -in @('all','weights'))   { Rebuild-Weights }

Ensure-Junctions
Write-Step '==== 重建流程完成 ===='
Write-Step '下一步: 运行 start_xinference_indextts.ps1 启动并验证 (首次启动权重组件需联网自动补齐)'
