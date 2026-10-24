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

- Web UI：<http://127.0.0.1:9997>
- OpenAI 兼容端点：<http://127.0.0.1:9997/v1>
- DSH-Tavern Provider Endpoint → `http://127.0.0.1:9997/v1`

启动脚本会：健康探测（`/status`）→ 未运行则启动 `xinference-local`（等待最多 4 分钟）→ 加载 IndexTTS-2.5（bf16，失败自动回退 IndexTTS2）。

---

## 目录结构

```
K:\SomeApps\Xinference\
├─ start_xinference_indextts.ps1    # 一键启动：服务 + 加载 IndexTTS-2.5（外层唯一启动脚本）
├─ start_xinference_indextts.bat    # 启动脚本的 .bat 封装
├─ config.ps1                       # 中央配置（启动脚本依赖：内部路径相对定位、外部引用明文）
├─ README.md                        # 本文件
├─ .gitignore
├─ scripts\                         # 工具脚本（均无绝对路径，读环境变量 / 相对定位）
│  ├─ install.ps1                   # 安装脚本
│  ├─ acl_fix.ps1                   # 应急工具：修复 System32 MSVC 运行库 ACL
│  ├─ launch_indextts.py            # 用 RESTfulClient 传 model_config 加载模型
│  └─ tts_test.py                   # 合成测试客户端
├─ logs\                            # 运行/安装日志（git 忽略）
├─ 说明文档\                         # 使用文档（05-外部引用与配置.md 含外部依赖明文清单）
├─ 问题记录\                         # 排障记录
├─ venv\                            # 虚拟环境（git 忽略，install.ps1 重建）
├─ .xinference\                     # 模型/数据/日志根（XINFERENCE_HOME，17.9GB，git 忽略）
└─ downloads\                       # 官方 cu128 wheel（git 忽略，脚本自动重下）
```

## 关键配置（已确认可用）

| 配置 | 值 | 原因 |
|---|---|---|
| 监听地址 | `--host 127.0.0.1` | 用 `0.0.0.0` 会让内部 health_check 连不上子进程（Cluster not available） |
| 就绪超时 | `XINFERENCE_HEALTH_CHECK_TIMEOUT=180` | 默认 10s 太短，实测 ~51s 就绪 |
| 鉴权 | `XINFERENCE_AUTH_ADVANCED=0` | 4.x 新增鉴权，本地直连关闭 |
| HF 镜像 | `HF_ENDPOINT=https://hf-mirror.com` | 国内下载 |
| Xet | `HF_HUB_DISABLE_XET=1` | 避免下载卡死 |
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
