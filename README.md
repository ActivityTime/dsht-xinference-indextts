# Xinference + IndexTTS-2.5 本地语音服务（DSH-Tavern 接入）

在 Windows + NVIDIA GPU 上部署 **Xinference 4.0.1**（模型服务底座）与 **IndexTTS-2.5**（角色语音合成），并通过 OpenAI 兼容端点接入 DSH-Tavern。

> 官方上游：
> - Xinference 底座：<https://github.com/xorbitsai/inference>（文档 <https://inference.readthedocs.io/>）
> - IndexTTS：<https://github.com/index-tts/index-tts>（权重 `IndexTeam/IndexTTS-2.5`，约 6GB）
>
> 本仓库只包含**部署脚本与排障文档**，不含 venv、wheel 与模型权重（见目录结构）。

---

## 环境要求

| 项 | 要求 | 实测 |
|---|---|---|
| 系统 | Windows 10/11 | Windows |
| Python | 3.13（cu128 wheel 为 cp313 构建） | 3.13.15 |
| GPU | NVIDIA（需 CUDA 12.8 驱动可用） | RTX 3080 10GB（sm_86） |
| 磁盘 | venv ≈ 5GB + wheel ≈ 2.7GB + 模型 ≈ 6GB | — |

> 注意：**不要使用豆包沙箱运行时自带的 python**；使用系统安装的 Python（本机路径 `K:\Users\ACTIM.DESKTOP-BPAEDTM\AppData\Local\Programs\Python\Python313\python.exe` 一类）。

---

## 快速安装

```powershell
# 1. 安装（创建 venv → 安装 xinference → 换装 cu128 torch → 验证 CUDA）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 2. 启动服务并加载 IndexTTS-2.5（首次自动下载权重约 6GB）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1
```

安装脚本参数：

```powershell
-Python <python.exe>   # 指定系统 Python 3.13（默认自动探测）
-WheelDir <目录>       # 使用本地已下载的 cu128 wheel（跳过下载）
-SkipTorch             # 跳过 cu128 torch 换装（不推荐）
```

## 使用

- Web 控制台（独立，推荐）：<http://127.0.0.1:9998>（随启动脚本自动拉起；状态总览 / 合成测试台 / 参考音频库）
- OpenAI 兼容端点：<http://127.0.0.1:9997/v1>
- DSH-Tavern Provider Endpoint → `http://127.0.0.1:9997/v1`

> 内置 WebUI 已拆除（与 IndexTTS-2.5 运行无关，问题记录 #32）：`http://127.0.0.1:9997/` 返回 JSON 404，服务纯 API 化。

启动脚本会：恢复自维护包（指纹一致跳过）→ 健康探测（`/status`）→ 未运行则启动 `xinference-local`（等待最多 4 分钟）→ 加载 IndexTTS-2.5（bf16；功能开关见 `config.ps1` 的 `USE_QWEN_EMO`/`QWEN_DEVICE`——文字情感默认关闭、首次请求惰性唤醒，功能不丢）→ 拉起 Web 控制台（9998）。**IndexTTS2（旧版）回退已移除**（见 问题记录 #19），只支持 IndexTTS-2.5。

---

## 一键复原（拿到自维护部分后）

本仓库只含**自维护部分**；`downloads\`（权重 + 两个 venv）全部可由可靠源一键重建。**需要下载的只有权重和 torch**；**Xinference 禁止下载**（本体在仓库 `maintained\xinference-package`，重建时整体恢复，不从 pip 安装）：

```powershell
# 1. 获取自维护部分（本仓库 / GitHub）
git clone https://github.com/ActivityTime/dsht-xinference-indextts.git
cd dsht-xinference-indextts

# 2. 一键重建下载区（停全部 Xinference 进程；联网；权重≈10.5GB + wheel≈2.6GB）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\rebuild_downloads.ps1

# 3. 启动并验证（自动应用自维护覆盖 + 恢复 junction + 拉起守护监控）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1
```

`rebuild_downloads.ps1` 参数：`-Target all|weights|venv-main|venv-v4`（默认 all）、`-Force`（强制重建）。

---

## 目录结构

```
K:\SomeApps\Xinference\                  # 自维护区（git → GitHub: ActivityTime/dsht-xinference-indextts）
├─ start_xinference_indextts.ps1/.bat    # 一键启动（9999 互斥锁 + 拉起守护监控 + Web 控制台）
├─ rebuild_downloads.ps1                 # 一键重建下载区（权重 HF + torch cu128 官方索引 + pip 依赖）
├─ config.ps1                            # 中央配置（内部路径相对定位、外部依赖明文、功能开关）
├─ console\admin.html                    # 独立 Web 控制台（单文件、无 CDN、shadcn 主题对齐旧内置 WebUI）
├─ README.md  .gitignore
├─ maintained\                           # 自维护资产（git 跟踪，无外部源）
│  ├─ xinference-package\                # 自维护 Xinference 包（禁止下载，Sync-Maintained 整包恢复；
│  │                                     #   thirdparty 已完全精简：#28 仅留 indextts+audiotools，2.3MB；
│  │                                     #   内置 WebUI 已拆除：#32 移除 ui/web/dist，服务纯 API 化）
│  ├─ requirements-venv-main.txt         # 主 venv 依赖清单（94 行，重建依据；不含 xinference）
│  └─ requirements-venv-v4.txt           # v4 模型环境依赖清单（151 行，重建依据）
├─ scripts\                              # launch_indextts.py / tts_test.py / monitor_indextts.py / console_server.py / install.ps1 / acl_fix.ps1
├─ 说明文档\  问题记录\  logs\backup\     # 文档与回滚点（git 跟踪）
├─ venv\            → junction → downloads\venv-main      # 主 venv（可下载区）
├─ .xinference\     → cache/v2/IndexTTS-2_5 & virtualenv/v4 为 junction → downloads
└─ downloads\                           # 可下载区（git 忽略；rebuild_downloads.ps1 一键重建）
   ├─ models\IndexTTS-2.5\              # 模型权重（HF IndexTeam/IndexTTS-2.5）
   ├─ venv-main\  venv-v4\              # 两个 venv（pip 重建）
   └─ torch-*.whl                       # cu128 wheel（download.pytorch.org 重下）
```

## 版本追踪（git）

本仓库用 **git** 管理并推送到 GitHub（`origin → github.com/ActivityTime/dsht-xinference-indextts`，main 分支）。

- **跟踪（自维护区）**：`maintained\`（`xinference-package` 自维护 Xinference 包 + 两个 requirements）、`scripts\`、`config.ps1`、`rebuild_downloads.ps1`、启动脚本、`说明文档\`、`问题记录\`、README、`.gitignore`、回滚点 `logs/backup/*.orig`。
- **忽略（可下载区）**：`downloads\`（权重/venv/wheel）、`.xinference\`、`logs\`（除 backup）、运行时产物（含 `.monitor.pid`）。
- **Xinference 禁止下载**：本体（含我们所有修改）整体存 `maintained\xinference-package\`，启动/重建时 `Sync-Maintained` 整包恢复到 venv（见 问题记录 #27）。
- **查看/回滚**：`git log --oneline`；`git diff <commit> -- <file>`；回滚改过的文件用 `maintained\xinference-override` 对应副本。
- **约定**：此后**每一步变更/操作先文档化（写入 问题记录 + 对应文档）再 commit，再 push**。

## 关键配置（已确认可用）

| 配置 | 值 | 原因 |
|---|---|---|
| 监听地址 | `--host 127.0.0.1` | 用 `0.0.0.0` 会让内部 health_check 连不上子进程（Cluster not available） |
| 就绪超时 | `XINFERENCE_HEALTH_CHECK_TIMEOUT=180` | 默认 10s 太短，实测 ~51s 就绪 |
| 鉴权 | `XINFERENCE_AUTH_ADVANCED=0` | 4.x 新增鉴权，本地直连关闭 |
| HF 镜像 | `HF_ENDPOINT=https://hf-mirror.com` | 国内下载 |
| Xet | `HF_HUB_DISABLE_XET=1` | 避免下载卡死 |
| 离线 | `HF_HUB_OFFLINE=1`、`MODELSCOPE_OFFLINE=1` | 本机自包含，禁用联网下载（见 问题记录 #22） |
| 健康探测 | `/status` | 4.x 中 `/health` 返回 404 |
| 模型精度 | `use_bf16=true` | 权重全精度，加载时 cast bf16（10GB 显存更稳） |

## 已知的坑（详见 问题记录/问题记录.md）

1. **torch 导入 PermissionError [WinError 5]**：System32 MSVC/OpenMP 运行库 ACL 损坏 → 用 `acl_fix.ps1` 修复。
2. **默认装的是 CPU torch**：RTX 无法使用 → `install.ps1` 自动换装 cu128（torch 2.11.0+cu128；cu128 索引最高 2.11.0，无 2.14.1）。
3. **pip 下载大轮易停滞**：install.ps1 用 `curl.exe` 直连 download.pytorch.org（实测 14-15MB/s）。
4. **阿里云 pytorch-wheels 无 Windows cp313 轮**：不要用它。
5. **wheel 文件名被截短会报 Invalid wheel filename**：必须用标准全名 `cp313-cp313-win_amd64`。
6. **`xinference launch` CLI 无法传 use_bf16**：必须用 `launch_indextts.py`（Python 客户端 model_config）。

## 许可与说明

- 本仓库部署脚本与文档：可自由使用（MIT）。
- 官方组件版权归各自上游（Xinference Apache-2.0；IndexTTS 见其仓库）。
- 敏感信息：仓库内脚本/文档经扫描不含密钥；日志不入库。
