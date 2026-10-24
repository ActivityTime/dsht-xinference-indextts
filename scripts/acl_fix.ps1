$ErrorActionPreference = 'Continue'
# 本脚本现位于 scripts\，日志写入上上级的 logs\（相对定位，禁止绝对路径）
$log = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\acl_fix.log'
"[===== FULL MSVC RUNTIME RESET $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====]" | Out-File -FilePath $log -Append -Encoding utf8

$pats = @(
  "C:\Windows\System32\msvcp140*.dll",
  "C:\Windows\System32\vcruntime140*.dll",
  "C:\Windows\System32\concrt140.dll",
  "C:\Windows\System32\vcomp140.dll",
  "C:\Windows\System32\msvcr140.dll"
)
$targets = @()
foreach ($pat in $pats) { $targets += Get-ChildItem -LiteralPath (Split-Path $pat) -Filter (Split-Path $pat -Leaf) -ErrorAction SilentlyContinue }

$seen = @{}
foreach ($t in $targets) {
    $d = $t.FullName
    if ($seen.ContainsKey($d)) { continue }; $seen[$d] = $true
    "[== $d ==]" | Out-File -FilePath $log -Append -Encoding utf8
    takeown /f $d /a 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
    icacls $d /reset 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
    icacls $d /grant "*S-1-5-32-545:(RX)" 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
}
"ALL DONE" | Out-File -FilePath $log -Append -Encoding utf8
