# Xinference + IndexTTS-2.5 本地语音服务（DSH-Tavern 接入）

在 Windows + NVIDIA GPU 上部署 **Xinference 4.0.1**（模型服务底座）与 **IndexTTS-2.5**（角色语音合成），通过 **OpenAI 兼容端点 + 自建适配层**接入 DSH-Tavern，配 **Web 控制台**管理。

> 官方上游：Xinference <https://github.com/xorbitsai/inference>（文档 <https://inference.readthedocs.io/>）· IndexTTS <https://github.com/index-tts/index-tts>（权重 `IndexTeam/IndexTTS-2.5` ≈6GB）
> 本仓库只含**自维护部分**（脚本/文档/自维护 Xinference 包/控制台）；venv、wheel、权重、本机配置均可一键重建（见「一键复原」）。

---
<img width="1200" height="934" alt="image" src="https://github.com/user-attachments/assets/1c8ab483-c6d0-45a8-a72b-d8052114678f" /> <img width="1195" height="921" alt="image" src="https://github.com/user-attachments/assets/30793602-a370-4a13-9b9d-c2b87758899f" />



## 项目特点

**1. 标准 OpenAI 端点 + 语义适配层（`POST /v1/audio/speech`，9998）**
DSHT / 任意 OpenAI 客户端零改动接入：`voice` 填**音色分组名**（或「分组名,情绪词」逗号组合）即自动解析参考音频；`instructions` 情绪指令解析；model 名可携带模式关键词按请求切换行为（如 `gpt-4o-切换音色`）。

**2. 五种适配模式**（内联 > model 关键词 > 全局默认，三级切换）：文字情感 / 引导音频（音色 A 情绪 B，标签命中成员音频做情绪引导、音色恒分组默认）/ 切换音色（音色与情绪同取标签成员音频）/ 情绪向量（触发词表命中注入 8 维向量）/ 情绪映射（条目化映射，见 4）。

**3. 四类内联指令**（每类首个生效，命中即从文本剔除）：`#[适配模式][..]`、`#[情感控制][情绪向量|文字描述|引导音频][..]`、`#[音色选择][库名/相对路径]`（最高级覆盖音色）、`#[参数][k=v]..`——单条请求内精细覆盖，未识别语法原样保留。

**4. 可视化映射配置**：音色分组（成员 ↑↓ 排序 = 标签匹配优先级；单向子串匹配）、情绪映射条目（绑定分组 + 有序子条目：标签-音频/向量/文字混排、音频可勾「替换默认音色」、未命中回退开关链、条目与子条目参数覆盖 JSON）、向量映射（条目级启用/停用）。

**5. Web 控制台**（9998，8 页）：状态总览（GPU/模型/缓存统计）、双模式合成测试台（14 项高级参数带默认值/建议范围/依据，留空=上游默认；最终 kwargs 预览）、参考音频库（多根可配、搜索、试听、「复制名称」一键取完整引用名）、请求历史归档（含失败，试听/删除）、引用体检。

**6. 引用唯一键与自愈**：音频外部引用一律 `库名/相对路径`（库名/分组名保存时校验唯一，抗库源重排）；失效引用进报告不再静默；「引用体检」一键扫全部条目。

**7. 性能与资源**：gpt.pth mmap 加载（内存峰值约减半）、同参数 LRU 缓存（参考音频按内容哈希，改名/换客户端仍命中）、QwenEmotion 惰性唤醒（默认不占 1.2-2.5G 显存，功能不丢）、bf16。

**8. 可部署性**：仓库零机器路径（本机路径在 git 忽略的 `config.local.ps1`）；自维护 Xinference 包指纹同步（一致秒过）；`rebuild_downloads.ps1` 一键重建下载区（Xinference 禁止下载，本体随仓库走）。

> **完整语义文档（模式/指令/解析规则/端点契约）：[说明文档/09-控制台与适配层完整参考.md](说明文档/09-控制台与适配层完整参考.md)**

---

## 环境要求

| 项 | 要求 | 实测 |
|---|---|---|
| 系统 | Windows 10/11 | Windows |
| Python | 3.13（cu128 wheel 为 cp313 构建） | 3.13.15 |
| GPU | NVIDIA（CUDA 12.8 驱动可用） | RTX 3080 10GB（sm_86） |
| 磁盘 | venv ≈5GB + wheel ≈2.7GB + 模型 ≈6GB | — |

> 注意：**不要使用豆包沙箱运行时自带的 python**；使用系统安装的 Python 3.13。

## 快速安装

```powershell
# 1. 安装（创建 venv → 安装 xinference → 换装 cu128 torch → 验证 CUDA）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1

# 2. 启动服务 + 加载 IndexTTS-2.5（首次自动下载权重约 6GB）+ 拉起控制台
pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1
```

安装脚本参数：`-Python <python.exe>` / `-WheelDir <目录>`（本地 wheel 跳过下载）/ `-SkipTorch`。

## 使用

- **Web 控制台（推荐）**：<http://127.0.0.1:9998>（随启动脚本自动拉起；归档/缓存/日志落 `console/`，git 忽略）
- **DSH-Tavern Provider Endpoint → `http://127.0.0.1:9998/v1`**（控制台适配层：voice=音色分组名/「分组名,情绪词」、情绪指令解析、四类内联指令；模型名填 `gpt-4o-mini-tts` 才会触发 DSHT 发送情绪指令）
- 直连端点（无适配层，仅 multipart + prompt_speech 可用）：<http://127.0.0.1:9997/v1>

> 内置 WebUI 已拆除（与 IndexTTS-2.5 运行无关，问题记录 #32）：9997 根路径 JSON 404，服务纯 API 化。IndexTTS2（旧版）回退已移除（#19），仅支持 IndexTTS-2.5。

启动脚本会：恢复自维护包（指纹一致跳过）→ 健康探测（`/status`）→ 未运行则启动 `xinference-local`（等待最多 4 分钟）→ 加载 IndexTTS-2.5（bf16；功能开关 `USE_QWEN_EMO`/`QWEN_DEVICE` 见 `config.ps1`——文字情感默认关闭、首次请求惰性唤醒）→ 拉起 Web 控制台（9998）→ 守护监控。

---

## 一键复原（拿到自维护部分后 / 新机器）

本仓库只含**自维护部分**；`downloads\`（权重 + 两个 venv）全部可由可靠源一键重建。**需要下载的只有权重和 torch**；**Xinference 禁止下载**（本体在仓库 `maintained\xinference-package`，重建时整体恢复，不从 pip 安装）：

```powershell
# 1. 获取自维护部分
git clone https://github.com/ActivityTime/dsht-xinference-indextts.git
cd dsht-xinference-indextts

# 2. 本机专属路径写入 config.local.ps1（git 忽略；UTF-8 带 BOM；示例见 config.ps1 注释）
#    $Script:RefAudioLib = '<你的参考音频库绝对路径>'

# 3. 一键重建下载区（停全部 Xinference 进程；联网；权重≈10.5GB + wheel≈2.6GB）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\rebuild_downloads.ps1

# 4. 启动并验证（恢复自维护覆盖 + 拉起控制台 + 守护监控）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1
```

`rebuild_downloads.ps1` 参数：`-Target all|weights|venv-main|venv-v4`（默认 all）、`-Force`。

---

## 目录结构

```
<部署根>\                                # 自维护区（git → GitHub: ActivityTime/dsht-xinference-indextts）
├─ start_xinference_indextts.ps1/.bat    # 一键启动（9999 互斥锁 + 守护监控 + Web 控制台）
├─ rebuild_downloads.ps1                 # 一键重建下载区（权重 HF + torch cu128 + pip 依赖）
├─ config.ps1                            # 中央配置（路径相对定位、功能开关；自动加载 ↓）
├─ config.local.ps1                      # 本机专属配置（git 忽略；参考音频库路径等）
├─ console\                              # Web 控制台（自包含；#32–#56）
│  ├─ admin.html                         #   页面（状态/合成×2模式/音色库/情绪映射/音色分组/向量映射/API配置/历史）
│  ├─ server.py                          #   后端（9998；/v1/audio/speech 适配层 + 管理 API + 归档/缓存/日志）
│  └─ library.json / *_defaults.json 等  #   运行时配置与产物（git 忽略）
├─ maintained\                           # 自维护资产（git 跟踪，无外部源）
│  ├─ xinference-package\                # 自维护 Xinference 包（禁止下载；含 mmap/bf16/高级参数等修改；
│  │                                     #   thirdparty 已精简至 2.3MB；内置 WebUI 已拆除）
│  ├─ requirements-venv-main.txt         # 主 venv 依赖清单（重建依据；含控制台的 fastapi/uvicorn/requests）
│  └─ requirements-venv-v4.txt           # v4 模型环境依赖清单
├─ scripts\                              # install.ps1 / launch_indextts.py / monitor_indextts.py / tts_test.py / acl_fix.ps1
├─ 说明文档\（01–09） 问题记录\           # 文档与排障史（56 条；09 为控制台/适配层权威语义）
├─ venv\            → junction → downloads\venv-main
└─ downloads\                           # 可下载区（git 忽略；rebuild 一键重建：权重/两 venv/torch wheel）
```

## 版本追踪（git）

本仓库用 **git** 管理并推送到 GitHub（`origin → github.com/ActivityTime/dsht-xinference-indextts`，main 分支）。

- **跟踪**：`maintained\`、`scripts\`、`console\`（server.py/admin.html）、`config.ps1`、启动/重建脚本、`说明文档\`、`问题记录\`、README、`.gitignore`、回滚点 `logs/backup/*.orig`。
- **忽略**：`downloads\`、`.xinference\`、`logs\`（backup 除外）、`console\` 运行时产物（history/cache/日志/各 JSON 配置）、`config.local.ps1`、`.monitor.pid`。
- **查看/回滚**：`git log --oneline`；`git diff <commit> -- <file>`。
- **约定**：每一步变更/操作**先文档化（问题记录 + 对应文档）再 commit 再 push**。

## 关键配置（已确认可用）

| 配置 | 值 | 原因 |
|---|---|---|
| 监听地址 | `--host 127.0.0.1` | `0.0.0.0` 会让内部 health_check 连不上子进程 |
| 就绪超时 | `XINFERENCE_HEALTH_CHECK_TIMEOUT=180` | 默认 10s 太短，实测 ~51s 就绪 |
| 鉴权 | `XINFERENCE_AUTH_ADVANCED=0` | 4.x 新增鉴权，本地直连关闭 |
| HF 镜像 / Xet | `HF_ENDPOINT=hf-mirror.com`、`HF_HUB_DISABLE_XET=1` | 国内下载 / 避免卡死 |
| 离线 | `HF_HUB_OFFLINE=1`、`MODELSCOPE_OFFLINE=1` | 本机自包含（#22） |
| 健康探测 | `/status`（`/health` 404）；**模型列表在 `/v1/models`**（#55） | 4.x 行为 |
| 模型精度 | `use_bf16=true` | 权重全精度加载时 cast bf16（10GB 显存更稳） |

## 已知的坑（详见 问题记录/问题记录.md，56 条）

1. **torch 导入 WinError 5**：System32 MSVC/OpenMP ACL 损坏 → `acl_fix.ps1`。
2. **默认 CPU torch**：`install.ps1` 自动换装 cu128（torch 2.11.0+cu128）。
3. **pip 大轮停滞**：install.ps1 用 curl 直连 download.pytorch.org。
4. 阿里云 pytorch-wheels 无 Windows cp313 轮；wheel 文件名截短会报 Invalid wheel filename。
5. **`xinference launch` CLI 传不了 use_bf16** → `launch_indextts.py`（Python 客户端）。
6. **PS1 文件必须 UTF-8 带 BOM**（无 BOM 中文注释被 PS5.1 误读静默失效，两犯：#32/#56）。
7. **IndexTTS-2.5 无参考音频直接拒绝**（零样本克隆架构，无预设音色/文字造声——控制台分组机制补位）。

## 许可与说明

- 部署脚本与文档：可自由使用（MIT）。官方组件版权归上游（Xinference Apache-2.0；IndexTTS 见其仓库）。
- 敏感信息：仓库经扫描不含密钥；日志不入库；本机路径仅存 `config.local.ps1`（不外发）。
