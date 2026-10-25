# 本地动漫角色 TTS 方案 · 文档总索引

> 项目：DSH-Tavern 本地动漫角色语音合成（IndexTTS-2.5 + Xinference）
> 整理日期：2026-10-24 · 存放位置：`<部署根>\说明文档`
> 一句话结论：**用 Xinference 4.0.1 当底座跑 IndexTTS-2.5（bf16），对 DSH-Tavern 提供 OpenAI 兼容 `/v1/audio/speech`，零样本克隆 + 免逐字稿 + 情绪控制三项全实测通过。**

---

## 一、这套方案是什么

为 DSH-Tavern 跑团构建**本地动漫角色语音**。全链路：
`DSH-Tavern → Xinference 4.0.1（OpenAI 兼容 API）→ IndexTTS-2.5（bf16）→ 参考音频库（约战/原神等）→ 22.05kHz wav`

- 零样本克隆：任意角色给一段干净参考音频即出声，免训练
- 免逐字稿：`prompt_speech` 只传参考音频 + 文本即可（实测确认）
- 情绪控制：8 维向量 `emo_vector` / 文本 `emo_text` / 音频 `emo_audio_prompt`，音色-情绪解耦
- 全部本地，无云端、无 API 费用，授权对个人自用免费

---

## 二、文档导航（本目录）

| 文件 | 内容 | 用途 |
|---|---|---|
| `README.md` | 本索引 | 总览 + 导航 |
| `01-整体方案架构.md` | 分层架构、数据流、关键参数、VRAM 仲裁、DSH 接入、排障结论 | 理解"为什么这么搭、怎么跑" |
| `02-参考音频库清单.md` | 声音资产全清单（IP × 角色 × 语种 × 段数） | 挑角色参考音频 |
| `03-落地与运维手册.md` | 启动 / 加载 / 合成测试 / DSH 接入 / 磁盘迁移 / 排障 | 日常运维速查 |
| `04-使用文档.md` | 手把手操作指南：启动→验证→三种合成方式→配置全表→情绪实操→DSH 接入 | **日常使用首选** |
| `05-外部引用与配置.md` | 配置化规则 + 外部依赖明文清单 + 搬文件夹步骤 | 自包含/移植时看 |
| `06-文件夹结构.md` | 完整目录树 + 文件归属/git 跟踪 + 可重建性 | 摸清部署根结构 |
| `07-IndexTTS-2.5功能开关与动态加载方案.md` | 权重→功能→代码映射、开关矩阵、惰性加载语义 | 功能开关/瘦身时看 |
| `08-IndexTTS-2.5功能与必要模块.md` | 功能清单 × 必要模块 × 使用参数（含五种调用示例） | **功能速查/接功能时看** |
| `09-控制台与适配层完整参考.md` | Web 控制台 8 页 × 五种适配模式 × 四类内联指令 × 音频引用格式 × 端点契约 × 可部署性 | **控制台/适配层权威语义（#32–#56）** |
| `整体方案架构图.html` | 可视化架构图（完整链路 + VRAM 仲裁） | 一眼看懂 |
| `Xinference项目架构图.html` | 底座（Xinference）内部部署图 | 排障时看底座细节 |
| `官方仓库.txt` | 官方 git / 文档 / 权重地址 | 溯源 |

---

## 三、关键速查（别去翻文档）

| 项 | 值 |
|---|---|
| 控制台（推荐入口） | `http://127.0.0.1:9998`（Web 管理 + 适配层 `/v1/audio/speech`） |
| DSH-Tavern 接入 | Provider Endpoint → `http://127.0.0.1:9998/v1`，模型名 `gpt-4o-mini-tts`（voice=分组名；详见 `09`） |
| 直连端点 | `http://127.0.0.1:9997/v1`（无适配层，仅 multipart + prompt_speech） |
| 启动命令 | `xinference-local --host 127.0.0.1 --port 9997` |
| 环境变量 | `XINFERENCE_HEALTH_CHECK_TIMEOUT=180`、`XINFERENCE_AUTH_ADVANCED=0`、`HF_ENDPOINT=https://hf-mirror.com`、`HF_HUB_DISABLE_XET=1` |
| 模型 | IndexTTS-2.5（全精度 fp32 ~6GB，无量化；`use_bf16=True` 为加载时 cast） |
| 语种 | `ZH / EN / JA / ES / AR` |
| 情绪向量 | `[happy,angry,sad,afraid,disgusted,melancholic,surprised,calm]` 8 维 |
| 一键启动 | `start_xinference_indextts.ps1` / `.bat`（起 9997 → 加载模型 → 起控制台 9998 → 守护） |
| 合成测试 | 控制台「语音合成」页（双模式）；或 `python scripts/tts_test.py <参考wav> <文本> <输出> <JA\|ZH> [emo_vector_json]` |
| 参考音频库 | `I:\SomeResources\VoiceClonePrompt-ForOmniVoice\prompt`（详见 `02`） |

---

## 四、原始调研/排障文档（位于项目目录，非本目录）

这些是选型与踩坑的原始记录，本目录的 `01/03` 已提炼其结论，需要细节时按需回查：

- `G:\...\【角色语音合成】\TTS选型结论与实施方案.md` —— 需求基线 + 候选引擎逐条结论 + 决策推演
- `...\IndexTTS专项调查-2026-10-24.md` —— IndexTTS 系列专项（版本谱系/架构/能力/授权/部署）
- `...\IndexTTS-2.5选型结论-2026-10-24.md` —— 含实测验证小节
- `...\本地动漫角色TTS调研-2026-10-22.md` / `...\TTS引擎选型报告-2026-10-24.md` —— 早期全景调研
- `...\动漫角色语音合成方案.txt` —— 社区调研长文（Qwen3/OmniVoice/IndexTTS/Fish Drama3 等横向对比）
- `...\formatted-omnivoice-dsht-guide.md` / `.html` —— OmniVoice Studio（VoiceStudio）DSHT 对接指南
- `...\OmniVoice部署调研.md` / `...\OmniVoice_Windows整合包调研.md` —— OmniVoice 落地调研
- `...\VoiceStudio后端WinError5根因与处置-2026-10-24.md` —— System32 VCOMP140.DLL ACL 损坏根因（已弃用 VoiceStudio）
- `<部署根>\问题记录\问题记录.md` —— 全量排障记录（13 条）

> 以上原始文档未移动、未改写，均保留原位，仅在本索引登记导航。

---

## 五、当前主线与待办

- **已全线跑通并实测**：日语克隆（四糸乃）、开心情绪、中文克隆（牧濑红莉栖），三份 wav 在项目目录。
- **数据收拢完成（2026-10-24）**：模型/运行数据 17.9GB 已从用户目录收拢到 `<部署根>\.xinference`，`XINFERENCE_HOME` 已设，旧目录已删。VRAM 保护阈值本地放宽（0.90→0.75）后模型加载与合成恢复正常（详见问题记录 #14）。
