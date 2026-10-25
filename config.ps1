# =============================================================
#  Xinference Central Config (config-driven, 2026-10-24)
#  Rules:
#   1) Internal paths are resolved RELATIVE to this file (NO absolute paths).
#      Move the whole folder without editing any script.
#   2) External dependencies are listed EXPLICITLY in $Script:ExternalDeps
#      (runtime deps, cannot be packaged/moved).
#  Chinese notes live in ..\说明文档\05-外部引用与配置.md (keep this file ASCII).
#  Must sit in the same dir as start_xinference_indextts.ps1 and be dot-sourced.
# =============================================================

# ---- Base dir: directory of this config file (= Xinference root) ----
$Script:BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- Internal paths (relative to BaseDir, NO absolute paths) ----
$Script:VenvDir        = Join-Path $Script:BaseDir 'venv'
$Script:XinferenceHome = Join-Path $Script:BaseDir '.xinference'
$Script:LaunchScript   = Join-Path $Script:BaseDir 'scripts\launch_indextts.py'  # 加载脚本(读环境变量,无硬编码)
$Script:TestScript     = Join-Path $Script:BaseDir 'scripts\tts_test.py'          # 合成测试客户端(读环境变量,无硬编码)

# ---- Service params ----
$Script:Port      = 9997
$Script:WebHost   = '127.0.0.1'          # MUST be 127.0.0.1, never 0.0.0.0
$Script:ModelName = 'IndexTTS-2.5'
$Script:Endpoint  = "http://$($Script:WebHost):$($Script:Port)"

# ---- Web 控制台（独立于内置 webui；内置 webui 已拆除，见 问题记录 #32）----
$Script:ConsolePort    = 9998
$Script:ConsoleScript  = Join-Path $Script:BaseDir 'console\server.py'
$Script:ConsoleDir     = Join-Path $Script:BaseDir 'console'   # 自包含：页面/后端/库配置/归档/缓存
$Script:ConsoleUrl     = "http://$($Script:WebHost):$($Script:ConsolePort)"

# ---- Env vars injected into the process (shared by server/launch/tts_test) ----
$env:XINFERENCE_HOME                 = $Script:XinferenceHome
$env:HF_ENDPOINT                     = 'https://hf-mirror.com'
$env:HF_HUB_DISABLE_XET              = '1'
$env:HF_HUB_OFFLINE                  = '1'    # 离线：禁止 HF 联网下载（本机已自包含模型）
$env:MODELSCOPE_OFFLINE              = '1'    # 离线：禁止 ModelScope 联网下载
$env:XINFERENCE_HEALTH_CHECK_TIMEOUT = '180'
$env:XINFERENCE_AUTH_ADVANCED        = '0'
$env:XINFERENCE_ENDPOINT             = $Script:Endpoint
$env:MODEL_NAME                      = $Script:ModelName

# ---- IndexTTS-2.5 功能开关（详见 说明文档\07-IndexTTS-2.5功能开关与动态加载方案.md）----
# USE_QWEN_EMO: false=启动不加载 QwenEmotion(文字情感 use_emo_text 专用, 1.2G 权重),
#               首个文字情感请求惰性唤醒(功能不丢, 仅该次请求慢几秒); true=启动即加载。
# QWEN_DEVICE : auto=跟随 accelerate(有 GPU 上 GPU); cpu=钉在内存, 省显存(短文本 +1~3s)。
$env:USE_QWEN_EMO                    = 'false'
$env:QWEN_DEVICE                     = 'auto'
$env:CONSOLE_PORT                    = $Script:ConsolePort
# 参考音频库根（只读使用）。本机专属绝对路径放 config.local.ps1（git 忽略），勿写进仓库：
#   config.local.ps1 内容示例：$Script:RefAudioLib = 'I:\SomeResources\VoiceClonePrompt-ForOmniVoice'
$Script:RefAudioLib = ''
$localCfg = Join-Path $Script:BaseDir 'config.local.ps1'
if (Test-Path -LiteralPath $localCfg) {
    . $localCfg
} else {
    # 自动生成模板（UTF-8 带 BOM——PS5.1 无 BOM 中文注释会被误读静默失效，#56/#59）：
    # 部署即开箱可用；用户填好路径后无需重启——控制台音色库页可直接改（写入 library.json）。
    $tpl = @(
        '# 本机专属配置（git 忽略）。config.ps1 缺失时会自动重建本模板。',
        '# 填参考音频库根目录后保存；也可不改这里，直接在控制台「参考音频库→库源配置」页填写。',
        '$Script:RefAudioLib = '''''
    ) -join [Environment]::NewLine
    [IO.File]::WriteAllText($localCfg, $tpl + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $true))
    Write-Host '[i] 已生成 config.local.ps1 模板（本机路径配置，git 忽略）——首次部署请按需填写'
}
$env:REF_AUDIO_LIB                   = $Script:RefAudioLib

# ---- External dependency list (explicit; runtime or usage-time resources) ----
# Chinese full notes: ..\说明文档\05-外部引用与配置.md
# Only User-level XINFERENCE_HOME may be an absolute path (Xinference requires it,
# points to data root); scripts always recompute from BaseDir above.
$Script:ExternalDeps = [ordered]@{
  'BasePython313 (venv base_prefix, runtime)' = 'K:\Users\ACTIM.DESKTOP-BPAEDTM\AppData\Local\Programs\Python\Python313'
  'MSVC_OpenMP_runtime (C:\Windows\System32)' = 'vcruntime140*.dll, msvcp140*.dll, concrt140.dll, vcomp140.dll, msvcr140.dll'
  'NVIDIA_CUDA_driver (GPU, runtime)' = 'RTX 3080 (sm_86); torch 2.11.0+cu128; NVIDIA driver installed'
  'ReferenceAudioLibrary (usage-time, do NOT move)' = 'I:\SomeResources\VoiceClonePrompt-ForOmniVoice'
}
