# 08 · IndexTTS-2.5 功能清单与必要模块

> 整理日期：2026-10-24
> 定位：本文档回答「IndexTTS-2.5 有哪些功能、每个功能需要加载哪些模块、怎么用」。使用方式详细步骤见 `04-使用文档.md`；模块开关与动态加载方案见 `07-IndexTTS-2.5功能开关与动态加载方案.md`；本文档与两者配合，不重复展开。
> 部署形态：Xinference 4.0.1（`xinference-local --host 127.0.0.1 --port 9997`）+ IndexTTS-2.5（`use_bf16=true`）
> 权重路径：`<部署根>\.xinference\huggingface\models--IndexTeam--IndexTTS-2.5\snapshots\<commit>\`

---

## 一、模型概述

IndexTTS-2.5 是 Bilibili IndexTeam 的**零样本（zero-shot）TTS** 模型：用**一段参考音频**克隆音色并合成语音。

- **架构**：自回归 GPT 主干 + flow-matching 语音→mel 解码器 + BigVGAN 声码器
- **参数规模**：~0.8B（GPT 主干）
- **支持语言（5 种）**：中文（ZH）、英文（EN）、日文（JA）、西班牙语（ES）、阿拉伯语（AR）
- **能力**：跨语言音色迁移、情感控制（与音色解耦）、语速控制、发音级控制（拼音/CMU 音素/假名）
- **输出**：22.05 kHz 波形（注：本地 `config.yaml` 采样率为 24000，以 config 为准）
- **推理要求**：NVIDIA GPU + 约 6GB 显存；`use_bf16=true` 加载实测空闲占用 ~8.3GB/10GB

---

## 二、权重组成（本地实测）

| 文件 | 模块 | 作用 | 原始精度 | 大小 |
|---|---|---|---|---|
| `gpt.pth` | **GPT 主干** | 自回归生成语音 token（文本/语义+音色+情感条件） | fp32（456 tensors） | 3.0 GB |
| `qwen0.6bemo4-merge\model.safetensors` | **语义模型**（Qwen 0.6B MoE 融合） | 多语言文本/语义理解 | bf16（310 tensors） | 1.14 GB |
| `codec.pth` | **语义编解码器** | 音频语义 codec | fp32 | 0.58 GB |
| `s2mel.pth` | **语音→mel 解码器** | flow-matching 生成 mel 谱 | fp32 | 0.40 GB |
| `feat1.pt` / `feat2.pt` | 说话人特征 | 音色特征（配合说话人编码器） | — | ~0.5 MB |
| `wav2vec2bert_stats.pt` | 语义统计 | 语义模型归一化统计 | — | <1 MB |
| `multilingual_zh_ja_yue_char_del.tiktoken` | 多语言 tokenizer | 中/英/日/西/阿 文本分词 | — | 0.9 MB |
| `config.yaml` | 模型配置 | GPT/条件模块/情感模块结构定义 | — | <1 MB |

> 推理时还会加载辅助模块：**BigVGAN 声码器**（mel→波形）、**ECAPA-TDNN（campplus）说话人编码器**（提取音色嵌入）——由 Xinference 内置的 IndexTTS 运行时（`thirdparty\indextts\`）提供。
> 合计权重 ≈ 5.1 GB。

---

## 三、功能清单（功能 → 必要模块 → 使用参数）

| 功能 | 说明 | 必要模块（权重/组件） | 使用参数 |
|---|---|---|---|
| **基础 TTS** | 文本→语音 | 全部核心：Qwen 语义 + GPT + s2mel + codec + BigVGAN + tokenizer | `input` |
| **音色克隆**（zero-shot） | 一段参考音频克隆音色 | + 说话人编码器（campplus/ECAPA）+ 说话人特征（feat）+ spk_matrix | `prompt_speech`（参考音频字节） |
| **跨语言音色迁移** | 用中文音色说英文/日文等 | + 多语言 tokenizer（Qwen 语义，5 语言） | `prompt_speech` + `language=ZH/EN/JA/ES/AR` |
| **情感控制**（音频参考） | 用一段带情绪的音频引导情感 | + 情感条件模块（emo_condition_module）+ 情感参考音频输入 | `emo_audio_prompt` |
| **情感控制**（向量） | 8 维情绪强度指定 | + emo 条件模块 | `emo_vector=[快乐,愤怒,悲伤,恐惧,厌恶,忧郁,惊讶,平静]` |
| **情感控制**（文本引导） | 用文本描述引导情感 | + **`use_qwen_emo=True`**（启动时）+ emo 条件模块 + Qwen 情感向量 | `use_emo_text=True` + `emo_text="..."` / `use_random` |
| **情感强度调节** | 情感影响程度 | —（推理参数） | `emo_alpha=0.0–1.0`（默认 1.0；文本情感建议 ~0.6） |
| **语速控制** | 语速快慢 | —（推理参数，无额外模块） | `speed=0.5–2.0`（OpenAI 语义，>1 加速）或 `duration_factor`（>1 变慢） |
| **发音控制** | 多音字/拼音/音素/假名精确读法 | —（文本前端，无额外模块） | `<词|读音>` 标注，如 `他在银<行|XING2>里<行|HANG2>走了半天。` |

---

## 四、使用方式（精简版，详细见 04）

### 4.1 Xinference Python 客户端（推荐）

```python
from xinference.client import Client

client = Client("http://127.0.0.1:9997")
model = client.get_model("IndexTTS-2.5")

# ① 基础音色克隆（必须给参考音频）
with open("prompt.wav", "rb") as f:
    prompt_speech = f.read()

wav = model.speech(
    input="你好，这是音色克隆演示。",
    prompt_speech=prompt_speech,   # 参考音频（克隆音色）
    language="ZH",                 # ZH / EN / JA / ES / AR
    speed=1.0,                     # 0.5–2.0，>1 加速
)
with open("out.wav", "wb") as f:
    f.write(wav)

# ② 跨语言音色迁移：用同一音色说英文
wav_en = model.speech(
    input="Hello, this is a cross-lingual voice cloning demo.",
    prompt_speech=prompt_speech,
    language="EN",
)

# ③ 情感控制（情感参考音频 + 强度）
with open("emo_sad.wav", "rb") as f:
    emo_prompt = f.read()
wav_emo = model.speech(
    input="真遗憾，他没能进入决赛。",
    prompt_speech=prompt_speech,
    language="ZH",
    emo_audio_prompt=emo_prompt,   # 情感参考音频
    emo_alpha=0.9,                 # 情感影响强度 0.0–1.0
)

# ④ 情感控制（8 维向量）：[快乐,愤怒,悲伤,恐惧,厌恶,忧郁,惊讶,平静]
wav_vec = model.speech(
    input="哇，我太幸运了！",
    prompt_speech=prompt_speech,
    language="ZH",
    emo_vector=[0, 0, 0, 0, 0, 0, 0.45, 0],  # 惊讶 0.45
    use_random=False,
)

# ⑤ 情感控制（文本引导，需启动时 use_qwen_emo=True）
wav_txt = model.speech(
    input="快躲起来！他来了！他来抓我们了！",
    prompt_speech=prompt_speech,
    language="ZH",
    use_emo_text=True,
    emo_text="你吓死我了！你是鬼吗？",
    emo_alpha=0.6,
    use_random=False,
)
```

### 4.2 OpenAI 兼容 SDK

```python
import openai

client = openai.Client(
    api_key="cannot be empty",
    base_url="http://127.0.0.1:9998/v1",  # 适配层（9997 直连需 multipart+prompt_speech）
)
response = client.audio.speech.create(
    model="IndexTTS-2.5",
    input="你好，OpenAI 兼容调用。",
    voice="",            # IndexTTS 需要 prompt_speech，voice 置空
    # 额外参数经 extra_body 传递
    extra_body={
        "prompt_speech": prompt_speech_bytes,   # 参考音频（raw body 时以 multipart 文件传）
        "language": "ZH",
        "speed": 1.0,
    },
)
response.write_to_file("out.wav")
```

### 4.3 cURL（HTTP 直调）

```bash
curl -X 'POST' 'http://127.0.0.1:9998/v1/audio/speech' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "IndexTTS-2.5",
    "input": "你好，这是 HTTP 调用演示。",
    "language": "ZH",
    "speed": 1.0,
    "stream": false
  }' \
  -F 'prompt_speech=@prompt.wav' \
  --output out.wav
```

### 4.4 模型加载（启动）

```python
from xinference.client import Client

client = Client("http://127.0.0.1:9997")
model_uid = client.launch_model(
    model_name="IndexTTS-2.5",
    model_type="audio",
    use_bf16=True,        # 关键：减少显存（实测空闲 ~8.3GB/10GB）
    # use_qwen_emo=True,  # 需要文本引导情感时才开启（增加显存）
)
```

> 当前部署由 `scripts\launch_indextts.py` 负责加载（`use_bf16=true`）；`scripts\tts_test.py` 是合成测试客户端。

---

## 五、语言处理机制（language 参数完整语义）

**核心：语言 token 引导单一多语言分词器**——不是按语言切换模型或分词器。全模型共用一套
多语言 BPE 词表（`multilingual_zh_ja_yue_char_del.tiktoken`，Whisper 风格 99 语言），
`language` 的硬作用是在**每段文本前拼语言 token 前缀**：`lang_prefix = '<|zh|> '` 等
（词表内置全部 `<|语言码|>` 特殊 token，未知 lang 兜底映射 `common`）。

`language` 的四个作用点（`infer_v2_5.py:729-742`）：

| 作用 | 逻辑 |
|---|---|
| ① 语言 token 前缀 | 每段 tokenize 前拼 `<|zh|> ` 等；分段 token 预算含前缀 |
| ② 文本规范化分派 | `zh/zhen/en` → 内置 ITN（数字/符号转读法，"3.14"→"三点一四"）；`ja/es` → NeMo 规范化；**其他语言不做规范化** |
| ③ 大小写处理 | `ja/zh/zhen/en` → 转小写；`es` → 转大写；其他不动 |
| ④ 发音标注/G2P | `<文字|发音>` 标注通用；`ja` 额外走 JapaneseG2P 假名处理 |

**支持的值**：词表层 99 语言码均可拼 token；**官方实测保证中/英/日/西/阿**（README）；
`zhen` = 中英混合（zh 规范化 + 接受中英混排）。

**不传 language 的行为（三层兜底，恒等于 ZH）**：
① 控制台适配层各分支 `kwargs.setdefault("language", ...)`（API 配置页 lang，默认 Auto）；
② wrapper `indextts2.py:189-192`：language/lang 都缺省时默认 `"ZH"`；
③ 结果 = 按 ZH 处理（`<|zh|>` 前缀 + 中文 ITN + 转小写）。

**语言 Auto（控制台，#60）**：API 配置页语言选 Auto 时，按 **input 主体语言字数**检测——
纯拉丁 → EN；假名占 CJK ≥1/4 → JA；其余 CJK → ZH（**中文兜底**）；空文本 → ZH。

**实用推论**：
1. **语言与音色/情绪完全解耦**——language 只作用于文本前端，参考音频决定音色；
   "日语文本 + 中文角色参考音频"合法（该音色读日语）；
2. **小语种风险在规范化**：语言 token 大概率能引导发音，但无 ITN——数字/符号/缩写按原样
   进分词器，读错比"不支持"更常见。小语种建议正文自写全（数字转文字）再合成；
3. **语种传错的症状是静默的**：日语文本按 ZH 处理不报错——假名仍能出声，但语言 token 错、
   数字按中文读法、ja G2P 不走，只有听输出才能发现。DSHT 不发 language（经适配层默认），
   日文台词需在分组条目参数 JSON 写 `{"language":"JA"}` 或内联 `#[参数][language=JA]`。

---

## 六、关键参数速查

| 参数 | 类型 | 范围/取值 | 说明 |
|---|---|---|---|
| `input` | str | — | 待合成文本 |
| `prompt_speech` | bytes | WAV 音频 | 参考音频（音色克隆必需） |
| `language` | str | `ZH`/`EN`/`JA`/`ES`/`AR`/`ZHEN`（混）/`Auto`* | 输出语言（*Auto 为控制台适配层选项，按 input 检测；不传恒等于 ZH，见第五节） |
| `speed` | float | 0.5–2.0 | OpenAI 语义，>1 加速 |
| `duration_factor` | float | 0.5–2.0 | 上游语义，>1 变慢（与 speed 二选一） |
| `emo_audio_prompt` | bytes | 音频 | 情感参考音频 |
| `emo_alpha` | float | 0.0–1.0（默认 1.0） | 情感影响强度（文本情感建议 ~0.6） |
| `emo_vector` | list[8] | 0–1 | [快乐,愤怒,悲伤,恐惧,厌恶,忧郁,惊讶,平静] |
| `use_emo_text` | bool | False/True | 启用文本情感引导 |
| `emo_text` | str | — | 情感文本描述 |
| `use_random` | bool | False/True | 随机情感 |
| `use_bf16` | bool（加载时） | False/True | bf16 加载省显存 |
| `use_qwen_emo` | bool（加载时） | False/True | 文本情感模型 QwenEmotion；本 fork 默认 False+首次请求惰性唤醒（#37，省 1.2-2.5G 显存功能不丢；上游原版才是必须加载时开） |

---

## 七、注意事项

1. **参考音频**：每次请求都必须提供（IndexTTS-2.5 是 zero-shot 克隆模型，没有预置音色）。
2. **发音控制语法**：`<词|读音>`，如 `他在银<行|XING2>里<行|HANG2>走了半天。`——拼音大写字母+声调数字。
3. **显存**：`use_bf16=true` 时空闲 ~8.3GB/10GB；开启 `use_qwen_emo` 会进一步增加，3080 10GB 请按需取舍。
4. **Python 版本**：上游官方支持 Python 3.10/3.11（Xinference 已内置独立虚拟环境处理，无需自己装）。
5. **情感能力**：真实情感控制使用独立输入（情感参考音频/向量/文本），与音色解耦；Xinference 的 IndexTTS-2.5 支持 `emo_audio_prompt`/`emo_vector`/`use_emo_text`（本 fork 文本引导**无需**加载时开 `use_qwen_emo`——首个请求惰性唤醒，见 #37）。

---

## 八、与相邻文档的关系

| 文档 | 主题 | 与本文档的关系 |
|---|---|---|
| `04-使用文档.md` | 完整使用流程（启动/验证/合成测试） | 本文档第四章是它的精简摘要 |
| `07-IndexTTS-2.5功能开关与动态加载方案.md` | 权重→功能→代码映射、开关设计、惰性加载 | 本文档第三章的「必要模块」是它「必须项/可选功能项」的清单视角 |
