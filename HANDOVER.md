# HANDOVER — Xinference + IndexTTS-2.5 本地语音服务 · 交接总结

> 交接时间：2026-10-25 · 仓库：`K:\SomeApps\Xinference`（git，61 commits，远端 GitHub `ActivityTime/dsht-xinference-indextts` main 分支，已全部推送）
> 本文件是给下一个 AI（或未来的你）的**现场入口**；细节都在仓库文档里，本文只指路+讲清不可见的约定。

---

## 一、这是什么项目（30 秒版）

Windows + NVIDIA GPU 上的**本地角色语音服务**：Xinference 4.0.1 底座跑 IndexTTS-2.5（零样本音色克隆 TTS），
外面包了一层我们自建的 **Web 控制台 + OpenAI 语义适配层**（端口 9998），DSH-Tavern / 任意 OpenAI 客户端
零改动接入。**核心价值在适配层**：voice 填"音色分组名"就自动解析参考音频，情绪指令/内联指令/五种适配模式。

```
DSH-Tavern ──→ POST :9998/v1/audio/speech（适配层：模式/分组/标签/内联/参数默认）
                    └──→ :9997 Xinference（直连端点，无适配）──→ IndexTTS-2.5
浏览器管理 ──→ :9998 控制台（8 页：状态/合成×2模式/音频库/情绪映射/音色分组/向量映射/API配置/历史）
```

## 二、必读文档（按此顺序）

| 顺序 | 文件 | 内容 |
|---|---|---|
| 1 | `README.md` | 项目特点八条 / 安装 / 一键复原 / 目录结构 / 已知的坑 |
| 2 | `说明文档/09-控制台与适配层完整参考.md` | **权威语义**：五模式表/四类内联指令/条目结构/端点契约/引用格式 |
| 3 | `说明文档/08-…功能与必要模块.md` 第五节 | language 参数完整语义（token 机制/兜底链/Auto 规则） |
| 4 | `问题记录/问题记录.md` | **62 条**：每个功能的决策、踩坑、验证记录——改任何东西前先查这里 |

## 三、系统现状（截至交接）

**运行中**：9997 服务 + 模型 IndexTTS-2.5（bf16）、9998 控制台、守护监控。工作区干净（无未提交改动）。

**功能全景**（对应问题记录编号）：
- 五种适配模式：文字情感 / 引导音频 / 切换音色 / 情绪向量 / 情绪映射（条目制）（#37-#50）
- 四类内联指令：`#[适配模式]` `#[情感控制][向量|文字|引导音频]` `#[音色选择]` `#[参数][k=v]`（#51/#54）
- 音频引用唯一键 `库名/相对路径`；库名/分组名保存查重；失效进报告 + 引用体检（#53/#54）
- API 配置页两块参数卡（OpenAI 4 项 + IndexTTS 14 项，空白=默认）+ 语言 Auto（ZHEN 兜底）（#60/#62）
- 归档/缓存/日志；gpt.pth mmap；QwenEmotion 惰性唤醒（省显存功能不丢）（#32-#36）
- 可部署：仓库零机器路径（config.local.ps1 缺失自动重建模板）；rebuild_downloads.ps1 一键复原（#56/#59）

**已知未做/可做**：情绪映射旧规则编辑器代码保留未删（#49 停用）；QwenEmotion 后台预热未做（首次文字情感请求慢几秒）；流式 stream 控制台不消费；内置 WebUI 已拆（不恢复）。

## 四、关键约定（违反会踩坑，均源自实测）

1. **每步变更先写问题记录 → commit → push**（git 走代理 `http://127.0.0.1:10808`：`git -c http.proxy=http://127.0.0.1:10808 push`）；
2. **PS1 文件必须 UTF-8 带 BOM**（PS5.1 无 BOM 中文注释被误读**静默失效**，两犯：#32/#56）；
3. **Bash→heredoc→Python 多层转义会吃反斜杠**：JS/文本内联一律 `chr(92)` 构造，别写 `\\n` 字面量（三犯：#43/#50/#53）；
4. **改 maintained 包（推理层）必须 cp 同步到 venv + 重启**；只改 console（server.py/admin.html）重启 9998 即可（admin.html 其实按请求读盘，刷新页面就生效）；
5. **改完 JS 必跑** `node -e "…new Function(…)"` 语法校验（多次靠它拦截）；改完 py 必跑 `py_compile`；
6. **测试请求带中文必须用 Python 客户端**（Git Bash→curl 传参 GBK 转换是伪影，#33）；历史断言用**唯一文本**定位自己的条目（页面可能有人在用）；
7. **大段重写函数后做 AST 局部变量盘点**（assigned vs used），#37/#43 两轮 500 的根因；**文件损坏时 git restore 回退到已提交状态再小步重做**，别在坏文件上叠补丁；
8. **外部引用 ID 一律唯一键**（库名/分组名，保存查重）——用户定的架构原则，新增可引用实体时照办；
9. 控制台配置 JSON（library/settings/emappings/vectormap/voice_groups/*_defaults）全部 git 忽略、机器专属；
10. Xinference 4.x：`/status` **不含**模型列表（在 `/v1/models`）；`/health` 404；就绪 ~51s；必须 `--host 127.0.0.1`。

## 五、常用操作

```powershell
# 启动（起服务+模型+控制台+守护；幂等，已在跑则跳过）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\start_xinference_indextts.ps1
# 只重启控制台（改了 console/ 之后）
#   杀 9998 进程 → 用 venv python 起 console/server.py（env: XINFERENCE_ENDPOINT/MODEL_NAME/CONSOLE_PORT/REF_AUDIO_LIB）
# 验证：curl http://127.0.0.1:9998/api/status（模型数/GPU）；合成走控制台或 /v1/audio/speech
```

**DSHT 接入**：Provider Endpoint `http://127.0.0.1:9998/v1`，模型名 `gpt-4o-mini-tts`（含 gpt-4o 才触发 DSHT 发情绪指令），voice=分组名或「分组名,情绪词」。

## 六、当前数据状态（用户侧）

- 测试用分组/条目大多已清理；`vector_map.json` 留有「太好了/庆祝→高兴向量」示例规则；情绪映射旧 `emotion_map.json` 有一条「开心」规则（已停用不消费）；这些用户可自行在页面增删；
- 参考音频库 = `I:\SomeResources\VoiceClonePrompt-ForOmniVoice`（在 config.local.ps1）；
- 用户此前在页面上编辑过分组数据（曾把测试分组改成只剩一个成员）——**验证前先 GET /api/groups 看实际数据**，别假设 fixture 还在。

---
*交接人：ZCode（GLM-5.3）· 全程记录见 问题记录 #1–#62*
