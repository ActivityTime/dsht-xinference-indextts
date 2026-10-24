# 07 - IndexTTS-2.5 功能开关与动态加载优化方案

> 目标：**不丢失任何 IndexTTS-2.5 功能**，把「每项功能 → 需加载的权重/模块」确认清楚，
> 做成可开关 + 动态加载，允许以关闭部分功能换取显存/内存/启动时间/磁盘占用下降。
> 依据：`thirdparty/indextts/infer_v2_5.py`（1084 行，v2.5 唯一推理入口）、
> `model/audio/indextts2.py`（Xinference 包装层）、模型目录实测大小。
> 行号均指当前 `maintained/xinference-package` 快照。

---

## 一、权重 → 功能 → 代码 完整映射（必须项确认）

### 1.1 必须项（基础合成管线，任何请求都走，不可关）

基础功能 = **零样本音色克隆合成**（`prompt_speech` 参考音频 + 文本 → 语音），
`spk_audio_prompt` 是强制参数（`indextts2.py:190` 直接抛错提示），因此下列组件全部必选：

| 权重/文件 | 磁盘 | 运行时组件 | 作用 | 加载点 |
|---|---|---|---|---|
| `gpt.pth` | 3.1G | `self.gpt`（UnifiedVoice） | 文本 → 语义码主干（含说话人条件、`merge_emovec`） | infer_v2_5.py:138-141 |
| `hf_cache/w2v-bert-2.0/model.safetensors` | 2.2G | `self.semantic_model`（Wav2Vec2Bert） | 参考音频 → 语义嵌入（克隆的音色来源） | :173-179 |
| `codec.pth` | 580M | `self.semantic_codec`（EnhancedCodec） | 语义码量化（参考音频）/解码（生成侧） | :185-189 |
| `s2mel.pth` | 396M | `self.s2mel`（MyModel/DiT cfm） | 语义 → mel 谱生成 | :194-202 |
| `hf_cache/bigvgan/` | 429M | `self.bigvgan` | mel → 波形 vocoder | :231-234 |
| `hf_cache/campplus_cn_common.bin` | 27M | `self.campplus_model` | 参考音频 → 说话人全局 style（192 维） | :221-223 |
| `wav2vec2bert_stats.pt` | 9.2K | `semantic_mean/std` | 语义嵌入归一化统计 | :180-182 |
| `multilingual_zh_ja_yue_char_del.tiktoken` | 887K | tokenizer + TextNormalizer + JapaneseG2P | 多语言文本前端（zh/ja/yue/en） | :237-246 |

> 推理管线（`:622-852`）验证：`spk_cond`（w2v-bert + campplus + s2mel length_regulator）
> → `gpt.inference_speech` → `semantic_codec.decode` → `s2mel cfm` → `bigvgan`。
> 缺任一项即无法合成，**全部保持常驻**。

### 1.2 可选功能项（按功能开关）

| 功能 | 需要的权重 | 磁盘 | 加载点 | 关闭后损失 |
|---|---|---|---|---|
| **文本情感**（`use_emo_text=True`，用文字描述情感 → 情感向量） | `qwen0.6bemo4-merge/`（QwenEmotion，0.6B fp16，`device_map=auto`） | **1.2G** | :133（`use_qwen_emo` 开）、:590-602 | 仅损失「文字情感描述」；情感音频参考与情感向量**不受影响** |
| **情感向量**（`emo_vector` 8 维手动指定） | `feat2.pt`（emo_matrix）+ `feat1.pt`（spk_matrix） | 423K | :248-256、:671-678 | 极小，建议常驻不做开关 |
| **情感参考音频**（`emo_prompt_speech`） | **无额外权重**（复用 gpt.merge_emovec :761 + semantic_model + campplus） | 0 | :684-697 | —（零成本，不存在关闭收益） |
| 术语表 | `glossary.yaml` | 0（当前不存在） | :243-246 | 无则跳过，已是惰性 |

**关键结论**：唯一的大体积可关项就是 **QwenEmotion（1.2G）**。它只服务
`use_emo_text` 一条路（`:585` 处 `use_emo_text or emo_vector` 会把 emo_audio_prompt
置空走向量路；`:590-602` 是唯一消费 `self.qwen_emo` 的地方），且 `:592-594` 已有
「未加载则报可操作错误」的守卫——**默认关闭不影响其余全部情感功能**。

### 1.3 非功能文件（与任何功能无关，不触碰「完整功能」红线）

| 文件 | 磁盘 | 性质 | 依据 |
|---|---|---|---|
| `hf_cache/w2v-bert-2.0/conformer_shaw.pt` | **2.2G** | 双格式冗余：加载走 HF `from_pretrained`（只读 `model.safetensors`） | 全树无代码引用（仅问题记录 #12 提及两文件均在） |
| `hf_cache/._____temp/` | **158M** | 下载临时残留（与 `semantic_codec_model.safetensors` 重复） | 目录名即 HF 下载临时区 |
| `hf_cache/semantic_codec_model.safetensors` | 169M | MaskGCT 素材：仅 `model_download.py:186-192` 在文件缺失时当自愈下载源；v2.5 运行时只读根目录 `codec.pth` | :191 + cli_v2.py:35（旧 CLI 清单） |

---

## 二、开关设计

### 2.1 开关矩阵

| 开关 | 类型 | 默认 | 节省 | 恢复方式 |
|---|---|---|---|---|
| `use_qwen_emo` | 启动配置（**已存在**，`indextts2.py:135` → `infer_v2_5.py:78/133`） | **false**（维持现状） | 显存 ~1.2-2.5G + 启动加载时间 + 首次磁盘读 1.2G | 请求 `use_emo_text=True` 时**惰性唤醒**（见 2.2），或改配置重启 |
| `qwen_device`（新增） | 启动配置：`auto`（=GPU）/`cpu` | `auto` | `cpu` 时省全部 Qwen 显存，代价短文本 1-3s CPU 推理延迟 | 改配置重启 |
| `enable_emo_matrix` | 不做（423K 常驻收益为零） | — | — | — |

### 2.2 动态加载（惰性唤醒）语义

- **磁盘权重永不删**（完整功能红线）：qwen、feat1/feat2、MaskGCT 素材全部留在模型目录。
- `use_qwen_emo=false` 时：启动不加载 QwenEmotion（现状已如此）。
- 请求带 `use_emo_text=True` 而 QwenEmotion 未加载时：**现场加载一次并缓存**（不再报错），
  本次与后续请求均可用——「默认关、按需唤醒、唤醒后不回落」。
- 唤醒是进程内单次的：不引入后台定时卸载（卸载/重载的碎片化显存与延迟抖动 > 收益）。

### 2.3 加载性能（功能无关，顺带收益）

1. `thirdparty/indextts/utils/checkpoint.py:24`：`torch.load(..., map_location='cpu')` →
   加 `mmap=True`——gpt.pth 3.1G 不再全量进 RAM，加载内存峰值约减半、更快。
2. `start_xinference_indextts.ps1`：`Sync-Maintained` 从「启动服务之后」**前移到启动之前**
   （顺带修正正确性：当前顺序下新启动的服务进程 import 的是同步前的旧包）；
   并改为包内容哈希一致则跳过 23MB 整删整拷。
3. 不开启 `use_torch_compile`（Windows CUDA triton 支持差、预热编译数分钟）、
   `use_accel`（需 flash_attn，cp313+cu128 无 Windows 轮）、`use_deepspeed`（Windows 装不上）。

---

## 三、改造点清单（按文件）

| # | 文件 | 改动 | 风险 |
|---|---|---|---|
| 1 | `thirdparty/indextts/utils/checkpoint.py` | `torch.load(..., mmap=True)` | 低（zip 格式 pth 均兼容；tts_test 验证） |
| 2 | `thirdparty/indextts/infer_v2_5.py` | ① `QwenEmotion` 构造传 `device_map=qwen_device`（`infer_v2_5.py:921-924`）；② 新增 `ensure_qwen_emo()`：`use_emo_text` 且未加载 → 现场按配置加载，替换 `:592-594` 的报错守卫 | 低（守卫已存在，改造是增量化） |
| 3 | `model/audio/indextts2.py` | `load()` 透传 `use_qwen_emo` / `qwen_device`（`:130-137`） | 低 |
| 4 | `scripts/launch_indextts.py` | `config` 增加 `use_qwen_emo` / `qwen_device`（读环境变量，默认同 config.ps1） | 低 |
| 5 | `config.ps1` | 中央配置新增两个开关（含注释：省显存 vs 文字情感功能的取舍） | 无 |
| 6 | `start_xinference_indextts.ps1` | Sync-Maintained 前移 + 哈希跳过 | 低 |
| 7 | `maintained/xinference-package/` 同步 | 上述 thirdparty/model 改动落在自维护包，需照 #27 惯例 Sync 回 venv（start 脚本已幂等恢复） | 无 |

**不改**：模型目录任何权重文件（含 1.2G qwen 与 169M MaskGCT 素材）；`rebuild_downloads.ps1`
下载清单保持完整（完整功能可随时重建）。

---

## 四、收益汇总

| 维度 | 改造前 | 改造后 |
|---|---|---|
| 显存（常驻） | gpt+semantic+s2mel+bigvgan+campplus+**qwen(fp16)** | 同左**减 qwen** → 省约 1.2-2.5G（3080 10GB 显著） |
| 模型加载 RAM 峰值 | 3.1G 全量 + 复制 | mmap 映射，约减半 |
| 启动时长 | qwen 加载 + 全量 torch.load | 跳过 qwen + mmap（就绪时间缩短） |
| 磁盘 | 11G 模型目录 | **不动**（完整功能红线）；如接受可另行清理非功能文件 2.4G（§1.3，另行确认） |
| 功能 | 全量 | **零损失**：文字情感默认关但可被请求惰性唤醒；情感音频/情感向量/音色克隆全保留 |

---

## 五、实施顺序（每步先文档化再 commit）

1. 本方案入库（说明文档/07）→ 问题记录 #29 立项 → commit。
2. 改 #1（mmap）→ 重启 + `tts_test.py` 合成验证 + 对比加载时长/内存 → commit。
3. 改 #2/#3/#4/#5（开关 + 惰性唤醒）→ 重启验证矩阵：
   a. 默认（qwen 不载）基础合成 HTTP 200；b. `use_emo_text=True` 请求触发惰性加载成功；
   c. `emo_prompt_speech` / `emo_vector` 路径正常；d. `nvidia-smi` 显存对比记录 → commit。
4. 改 #6（启动顺序 + 哈希跳过）→ 重启两次验证 → commit。
5. 回滚：惯例 `logs/backup/*.orig` + git revert；权重文件零改动，无回滚面。

---

*撰写：2026-10-24；依据快照：maintained/xinference-package @ 当前工作树。*
