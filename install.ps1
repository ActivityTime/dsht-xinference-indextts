<#
.SYNOPSIS
  Xinference + IndexTTS-2.5 一键安装（Windows）

.DESCRIPTION
  1) 定位系统 Python 3.13（勿用豆包沙箱运行时自带 python）
  2) 创建虚拟环境 venv/
  3) 升级 pip 并安装 xinference（默认会带 CPU 版 torch）
  4) 换装 cu128 torch（CPU 版在 NVIDIA GPU 上不可用）
  5) 验证 torch.cuda.is_available()

  装完后用 start_xinference_indextts.ps1 启动服务并加载 IndexTTS-2.5。

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -WheelDir K:\wheels
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Python "K:\...\Python313\python.exe"
#>
param(
  [string]$Python = '',
  [string]$WheelDir = '',
  [switch]$SkipTorch,
  [string]$Index = 'https://download.pytorch.org/whl/cu128'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root 'venv'
$Down = Join-Path $Root 'downloads'

function Write-Step($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-OK($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }

# ---------- 1. 定位系统 Python 3.13 ----------
Write-Step '定位 Python 3.13 ...'
$py = ''
if ($Python -and (Test-Path $Python)) { $py = $Python }
if (-not $py) {
  $cands = @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "C:\Python313\python.exe",
    "$env:ProgramFiles\Python313\python.exe"
  )
  foreach ($c in $cands) { if (Test-Path $c) { $py = $c; break } }
}
if (-not $py) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notmatch 'doubao|agent_mode') {
    $v = & $cmd.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
    if ($v -match '^3\.1[0-9]') { $py = $cmd.Source }
  }
}
if (-not $py) {
  Write-Warn '未找到系统 Python 3.13。请先安装，或用 -Python 参数指定 python.exe 路径。'
  Write-Warn '注意：不要使用豆包沙箱运行时自带的 python。'
  exit 1
}
$ver = & $py -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
if ($ver -notmatch '^3\.13') {
  Write-Warn "当前 python 版本 $ver，需要 3.13（cu128 wheel 为 cp313 构建）。"
  Write-Warn "如需其他 Python 3.x，请手动下载对应 cp 版本的 cu128 wheel 并用 -WheelDir 指定。"
  exit 1
}
Write-OK "Python: $py ($ver)"

# ---------- 2. 创建 venv ----------
Write-Step '创建虚拟环境 venv/ ...'
$vpy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $vpy)) {
  & $py -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Write-Warn 'venv 创建失败'; exit 1 }
}
Write-OK "venv: $vpy"

# ---------- 3. 升级 pip 并安装 xinference ----------
Write-Step '升级 pip 并安装 xinference（会带 CPU 版 torch，稍后换装） ...'
& $vpy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Warn 'pip 升级失败'; exit 1 }
& $vpy -m pip install xinference
if ($LASTEXITCODE -ne 0) { Write-Warn 'xinference 安装失败'; exit 1 }
Write-OK 'xinference 已安装'

# ---------- 4. 换装 cu128 torch ----------
if ($SkipTorch) {
  Write-Warn '已跳过 torch 换装（-SkipTorch）'
} else {
  # 版本与问题记录一致：cu128 索引最高 torch 2.11.0；URL 中 + 用 %2B 编码
  $wheels = @(
    'torch-2.11.0%2Bcu128-cp313-cp313-win_amd64.whl',
    'torchaudio-2.11.0%2Bcu128-cp313-cp313-win_amd64.whl',
    'torchvision-0.26.0%2Bcu128-cp313-cp313-win_amd64.whl'
  )
  $local = New-Object System.Collections.Generic.List[string]
  if ($WheelDir) {
    foreach ($w in $wheels) {
      $p = Join-Path $WheelDir ($w -replace '%2B', '+')
      if (Test-Path $p) { $local.Add($p) } else { Write-Warn "本地缺少 $($w -replace '%2B','+')，改用下载" }
    }
  }
  if ($local.Count -lt 3) {
    New-Item -ItemType Directory -Force -Path $Down | Out-Null
    foreach ($w in $wheels) {
      $dest = Join-Path $Down ($w -replace '%2B', '+')
      if (-not (Test-Path $dest)) {
        Write-Step "下载 $($w -replace '%2B','+')（约 2.7GB，pip 下载易停滞，用 curl 直连） ..."
        & curl.exe -L --retry 3 --retry-delay 3 -o $dest "$Index/$w"
        if ($LASTEXITCODE -ne 0) { Write-Warn "下载失败：$w"; exit 1 }
      }
      $local.Add($dest)
    }
  }
  Write-Step '强制重装 cu128 torch（--force-reinstall --no-deps，避免 CPU 版残留） ...'
  & $vpy -m pip install --force-reinstall --no-deps @($local)
  if ($LASTEXITCODE -ne 0) { Write-Warn 'torch 换装失败'; exit 1 }
  Write-OK 'cu128 torch 换装完成'
}

# ---------- 5. 验证 CUDA ----------
Write-Step '验证 CUDA 可用性 ...'
$cuda = & $vpy -c "import torch;print(torch.__version__);print(torch.cuda.is_available())" 2>&1
$cuda | ForEach-Object { Write-Host $_ }
if ("$cuda" -match 'True') {
  Write-OK 'CUDA 可用，安装完成。'
} else {
  Write-Warn 'torch.cuda.is_available() 非 True，请检查 NVIDIA 驱动与 CUDA 12.8 可用性。'
  Write-Warn '若为 ACL 权限问题（WinError 5），可运行 .\acl_fix.ps1 后重试。'
}

Write-Host ''
Write-Host '================ 安装完成 ================' -ForegroundColor Green
Write-Host '下一步：'
Write-Host '  pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1'
Write-Host '  （首次启动会自动下载 IndexTTS-2.5 权重约 6GB）'
Write-Host '=========================================='
