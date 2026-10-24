# -*- coding: utf-8 -*-
"""
IndexTTS-2.5 独立 Web 控制台后端（127.0.0.1:9998，自包含于 console/）。

- 页面：console/admin.html（单文件、无 CDN、样式对齐旧 Xinference 内置 WebUI）。
- 代理：/api/tts -> Xinference /v1/audio/speech（multipart 透传），音频字节原样回流。
- 归档：每次代理请求（含失败）写 console/history/<id>/：meta.json + 输出音频
  （+上传型参考音频的副本；库内参考音频只记相对位置不复制）。可试听/删除/清空。
- 缓存：同参数（含参考音频内容哈希）请求命中 console/cache/，跳过上游推理。
  LRU 上限（条数/字节）由 CONSOLE_CACHE_MAX_ENTRIES / CONSOLE_CACHE_MAX_BYTES 控制。
- 音色库：多根可配置（console/library.json），不依赖任何特定目录结构；
  首次运行用 REF_AUDIO_LIB 环境变量自动播种。列表按「根/子目录」两级分组。
- 日志：console/console.log（RotatingFileHandler 2MB x3）。

单机内网用途：只绑 127.0.0.1；文件接口做路径穿越防护；端口占用即退出（单实例）。
"""
import base64
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

CONSOLE_DIR = Path(__file__).resolve().parent
BASE_DIR = CONSOLE_DIR.parent
ADMIN_HTML = CONSOLE_DIR / "admin.html"
HISTORY_DIR = CONSOLE_DIR / "history"
CACHE_DIR = CONSOLE_DIR / "cache"
LIB_FILE = CONSOLE_DIR / "library.json"
LOG_FILE = CONSOLE_DIR / "console.log"

XINFERENCE_ENDPOINT = os.environ.get("XINFERENCE_ENDPOINT", "http://127.0.0.1:9997").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "IndexTTS-2.5")
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "9998"))
REF_AUDIO_LIB = os.environ.get("REF_AUDIO_LIB", "")  # 仅作 library.json 缺失时的播种默认
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
CACHE_MAX_ENTRIES = int(os.environ.get("CONSOLE_CACHE_MAX_ENTRIES", "200"))
CACHE_MAX_BYTES = int(os.environ.get("CONSOLE_CACHE_MAX_BYTES", str(1024 * 1024 * 1024)))  # 1GB
HISTORY_KEEP = int(os.environ.get("CONSOLE_HISTORY_KEEP", "500"))

for d in (HISTORY_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("console")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)

app = FastAPI(title="IndexTTS-2.5 Console", docs_url=None, redoc_url=None, openapi_url=None)


# ────────────────────────── 页面 / 基础 ──────────────────────────

@app.get("/")
def index():
    # no-cache：页面随代码更新，浏览器必须每次校验（否则改版后一直看旧缓存）
    return FileResponse(ADMIN_HTML, media_type="text/html; charset=utf-8",
                        headers={"cache-control": "no-cache"})


@app.get("/api/config")
def api_config():
    return {
        "endpoint": XINFERENCE_ENDPOINT,
        "model_name": MODEL_NAME,
        "use_qwen_emo": os.environ.get("USE_QWEN_EMO", "false"),
        "qwen_device": os.environ.get("QWEN_DEVICE", "auto"),
        "console_port": CONSOLE_PORT,
        "cache_enabled": True,
        "history_dir": str(HISTORY_DIR),
        "cache_dir": str(CACHE_DIR),
        "library_file": str(LIB_FILE),
    }


@app.get("/api/status")
def api_status():
    try:
        r = requests.get(XINFERENCE_ENDPOINT + "/status", timeout=5)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"Xinference 不可达: {e}"}, status_code=502)


# ────────────────────────── 合成代理（缓存 + 归档 + 日志） ──────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_lookup(key: str):
    for p in CACHE_DIR.glob(key + ".*"):
        if p.suffix != ".json":
            sidecar = CACHE_DIR / (key + ".json")
            if sidecar.is_file():
                p.touch()  # LRU: 命中刷新 mtime
                return p
    return None


def _cache_evict():
    files = [p for p in CACHE_DIR.iterdir() if p.suffix != ".json"]
    total = sum(p.stat().st_size for p in files)
    while len(files) > CACHE_MAX_ENTRIES or total > CACHE_MAX_BYTES:
        oldest = min(files, key=lambda p: p.stat().st_mtime)
        total -= oldest.stat().st_size
        (CACHE_DIR / (oldest.stem + ".json")).unlink(missing_ok=True)
        oldest.unlink(missing_ok=True)
        files.remove(oldest)


async def _read_form(request: Request):
    """multipart 解析：文本字段进 data，文件字段进 files，参考音频算内容哈希。"""
    form = await request.form()
    data, files = {}, {}
    ref_info = {"source": "unknown", "rel": None, "name": None, "sha256": None}
    for key, value in form.multi_items():
        if hasattr(value, "read"):
            content = await value.read()
            files[key] = (value.filename, content, value.content_type or "application/octet-stream")
            if key == "prompt_speech":
                ref_info = {"source": "upload", "rel": None, "name": value.filename,
                            "sha256": hashlib.sha256(content).hexdigest()[:16]}
        else:
            data[key] = value
    return data, files, ref_info


async def _proxy_tts(data: dict, files: dict, ref_info: dict, adapter_report: dict | None = None):
    """缓存 → 上游 → 归档 → 日志 → 响应（直接配置与 OpenAI 适配两条路共用）。"""
    t0 = time.perf_counter()
    cache_key = None
    if files.get("prompt_speech"):
        try:
            kwargs_obj = json.loads(data.get("kwargs") or "{}")
        except json.JSONDecodeError:
            kwargs_obj = {}
        canonical = json.dumps(
            {**{k: data.get(k, "") for k in ("model", "input", "voice", "response_format", "speed")},
             "kwargs": kwargs_obj, "ref": ref_info["sha256"]},
            sort_keys=True, ensure_ascii=False)
        cache_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    hit_path = _cache_lookup(cache_key) if cache_key else None
    if hit_path is not None:
        logger.info("CACHE HIT %s input=%r", cache_key[:12], data.get("input", "")[:60])
        headers = {"X-Cache": "hit", "X-Cache-Key": cache_key}
        if adapter_report is not None:
            headers["X-Adapter-Report"] = quote(json.dumps(adapter_report, ensure_ascii=False))
        return Response(content=hit_path.read_bytes(), status_code=200,
                        media_type="application/octet-stream", headers=headers)

    if not files.get("prompt_speech"):
        return JSONResponse({"detail": "缺少 prompt_speech（参考音频）"}, status_code=400)

    upstream_ms = None
    try:
        t_up = time.perf_counter()
        upstream = requests.post(
            XINFERENCE_ENDPOINT + "/v1/audio/speech", data=data, files=files, timeout=600)
        upstream_ms = round((time.perf_counter() - t_up) * 1000)
    except Exception as e:  # noqa: BLE001
        logger.error("UPSTREAM FAIL input=%r err=%s", data.get("input", "")[:60], e)
        return JSONResponse({"detail": f"Xinference 不可达: {e}"}, status_code=502)

    total_ms = round((time.perf_counter() - t0) * 1000)
    fmt = (data.get("response_format") or "wav").lower()
    ext = fmt if fmt in ("wav", "mp3", "flac", "ogg") else "bin"

    # 归档（成功与失败都留档）
    hid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    hdir = HISTORY_DIR / hid
    audio_name = None
    try:
        hdir.mkdir(parents=True, exist_ok=True)
        if upstream.status_code == 200 and upstream.content:
            audio_name = f"output.{ext}"
            (hdir / audio_name).write_bytes(upstream.content)
            if ref_info["source"] == "upload" and files.get("prompt_speech"):
                suffix = Path(files["prompt_speech"][0] or "ref.bin").suffix or ".bin"
                (hdir / f"input_ref{suffix}").write_bytes(files["prompt_speech"][1])
        meta = {"id": hid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": data.get("model"),
                "input": data.get("input"), "voice": data.get("voice"), "speed": data.get("speed"),
                "kwargs_raw": data.get("kwargs"), "ref": ref_info, "status": upstream.status_code,
                "bytes": len(upstream.content), "upstream_ms": upstream_ms, "total_ms": total_ms,
                "cache": "miss", "cache_key": cache_key, "audio": audio_name,
                "adapter": adapter_report}
        (hdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        _prune_history()
    except OSError as e:  # 归档失败不阻塞请求
        logger.error("ARCHIVE FAIL %s: %s", hid, e)

    # 写缓存（仅成功）
    if cache_key and upstream.status_code == 200 and upstream.content:
        try:
            (CACHE_DIR / f"{cache_key}.{ext}").write_bytes(upstream.content)
            (CACHE_DIR / f"{cache_key}.json").write_text(
                json.dumps({"input": data.get("input"), "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False), encoding="utf-8")
            _cache_evict()
            logger.info("CACHE STORE %s (%d bytes)", cache_key[:12], len(upstream.content))
        except OSError as e:
            logger.error("CACHE STORE FAIL: %s", e)

    logger.info("TTS status=%d upstream_ms=%s total_ms=%d bytes=%d input=%r",
                upstream.status_code, upstream_ms, total_ms, len(upstream.content),
                str(data.get("input", ""))[:60])
    headers = {"X-Cache": "miss", "X-Cache-Key": cache_key or ""}
    if adapter_report is not None:
        headers["X-Adapter-Report"] = quote(json.dumps(adapter_report, ensure_ascii=False))
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/octet-stream"),
                    headers=headers)


@app.post("/api/tts")
async def api_tts(request: Request):
    """直接配置路：multipart 透传（客户端组装全部字段），缓存 + 归档 + 日志。不做任何文本解析。"""
    data, files, ref_info = await _read_form(request)
    return await _proxy_tts(data, files, ref_info)


# ────────────────────────── OpenAI 适配层（对齐 K:\OmniVoice 的适配语义） ──────────────────────────
#
# DSHT/调用方按 OpenAI 兼容字段发请求：input 可携带情绪指令「用X的语气说这句话。」，
# voice 可填音色分组名。适配层依次：
#   ① 剥指令壳取情绪词（正则同 OmniVoice strip_style_sentence）；
#   ② 情绪映射表按短语最长匹配（命中短语从合成文本剥离，避免被朗读）；
#   ③ 命中规则 → 情感动作（向量/文字）注入 kwargs（调用方显式给的 emo_* 优先）；
#   ④ voice=分组名 → 按情绪标签联动组内成员（无标签/未命中用默认成员）取参考音频。
# 调用方在 kwargs 里显式给的参数永远优先，适配层只补空缺。

DSH_INSTRUCTION_RE = re.compile(
    r"^\s*用\s*([^，,。；;！!？?\s]{1,12}?)\s*的?\s*语气\s*"
    r"(?:(?:说|讲|念|朗读|读)\s*)?(?:这?句话?|这句话|这句|话)?\s*[。.！!？?]?\s*")


@app.post("/v1/audio/speech")
@app.post("/api/tts/adapter")
async def api_tts_adapter(request: Request):
    # 标准端点：OpenAI JSON 载体（DSHT/通用客户端）与 xinference multipart 载体
    # （prompt_speech 文件）都接受——适配层是为 API 准备的，两条路行为完全一致。
    """OpenAI 适配层（语义逐条对齐，#38 起分模式定义）。

    请求字段：model(忽略,路由本机模型) / input(纯正文) / instructions / voice /
    response_format / speed / kwargs(xinference 扩展 JSON) / prompt_speech 文件。

    【文字情感 text_emotion】（#38 第 1 条）：
      - voice：逗号分段。首个命中分组名的段确定分组（多个命中取第一个）；
        其余段与分组**成员情绪标签**做子串匹配，确定用分组中哪个音色（未命中用默认成员）；
        命中分组名的段不再参与标签匹配。
      - instructions：整段作为文字情感描述（emo_text）。
      - 情绪映射表在该模式不参与（既不匹配 voice，也不注入情感）。
      - 内联 #[模式][参数]（instructions/voice/input 任意字段）优先级最高，发送前剔除。

    【guide_audio / group_switch】保持 #37 语义（见下方分支实现）。
    优先级：内联 > 调用方显式 kwargs > 模式默认行为。
    """
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        data, files, ref_info = await _read_form(request)
    else:
        body = await request.json()
        data = {}
        for k, v in body.items():
            data[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        files = {}
        ref_info = {"source": "json", "rel": None, "name": None, "sha256": None}
    report = {"residual": None, "tag": "", "instruction": "", "emotion": None,
              "voice_resolved": None, "stripped": [], "inline": {}, "mode": None,
              "model": {"in": str(data.get("model") or ""), "used": MODEL_NAME}}
    data["model"] = MODEL_NAME
    voice = str(data.get("voice") or "").strip()
    try:
        kwargs = json.loads(data.get("kwargs") or "{}")
        if not isinstance(kwargs, dict):
            kwargs = {}
    except json.JSONDecodeError:
        kwargs = {}

    # ── 0) 内联 #[模式][参数] 覆盖（instructions/voice/input 三字段全扫，命中即剔除）──
    INLINE_RE = re.compile(r"#\[([^" + chr(92) + "[" + chr(92) + "]]{1,20})" + chr(92) + "]" +
                           chr(92) + "[([^" + chr(92) + "[" + chr(92) + "]]{1,400})" + chr(92) + "]")
    overrides = {}
    for fname in ("instructions", "voice", "input"):
        t = str(data.get(fname) or "")
        if not t:
            continue

        def _sub(m, fname=fname):
            mode, param = m.group(1).strip(), m.group(2).strip()
            key = {"文字情感": "emo_text", "情感向量": "emo_vector", "引导音频": "emo_ref",
                   "音色分组": "voice_group", "情绪标签": "tag"}.get(mode)
            if key is None:
                return m.group(0)  # 未识别 → 原样保留
            overrides[key] = param
            report["inline"][f"{fname}:{mode}"] = param
            return ""

        data[fname] = INLINE_RE.sub(_sub, t).strip()

    # ── 1) 模式判定（提前：voice/instructions 的解析方式取决于模式）──
    mode = _load_config("settings", {}).get("adapter_mode", "text_emotion")
    if mode not in ("text_emotion", "guide_audio", "group_switch"):
        mode = "text_emotion"
    report["mode"] = mode

    instructions = str(data.pop("instructions", "") or "").strip()
    text = str(data.get("input") or "")
    explicit_emo = any(k in kwargs for k in ("emo_vector", "use_emo_text", "emo_prompt_speech_b64"))
    explicit_ref = bool(files.get("prompt_speech"))
    groups_all = _load_config("groups", {"groups": []}).get("groups", [])

    def _member_audio(mm):
        roots = _load_library()
        ri = int(mm.get("root", 0))
        if 0 <= ri < len(roots):
            path = Path(roots[ri]["path"]) / str(mm.get("rel", ""))
            if path.is_file():
                return path
        return None

    # ── 2) 分支：文字情感（#38 第 1 条） ──
    if mode == "text_emotion":
        # voice：逗号分段 → 首个命中分组名的段定分组；其余段与成员标签子串匹配定音色
        voice_parts = [seg.strip() for seg in re.split(r"[，,]", voice)
                       if seg.strip() and seg.strip() != "default"]
        group = None
        group_consumed = set()
        for seg in voice_parts:
            # 命中分组名的候选中取最长名（「露娜的伤心音色」优先于短名「伤心音色」，
            # 避免子串包含关系下短名分组抢中）；命中段一律不再参与标签匹配
            cands = [g for g in groups_all
                     if g.get("name") and g.get("name") in seg]
            if cands:
                group_consumed.add(seg)  # 命中分组名的段一律不再参与标签匹配
                if group is None:
                    group = max(cands, key=lambda g: len(g.get("name", "")))  # 多个命中取第一个出现的段，段内取最长名
        matched_member = None
        if group is not None:
            remaining = [seg for seg in voice_parts if seg not in group_consumed]
            # 按分组内标签顺序（优先级）遍历：首个在任一剩余词组中命中的标签即确认
            # （用户例：标签序【不好笑】【笑】【伤心】时，「她很伤心」在前的词组不赢，
            #   优先级更高的【不好笑】命中「这一点也不好笑」胜出）
            for mm in group.get("members", []):
                label = str(mm.get("label", ""))
                if not label:
                    continue
                ll = label.lower()
                if any(ll in seg.lower() or seg.lower() in ll for seg in remaining):
                    matched_member = mm
                    break
        # 音色：命中的成员，未命中用分组默认成员
        default_member = next((mm for mm in (group or {}).get("members", []) if mm.get("is_default")), None) \
            or ((group or {}).get("members") or [None])[0]
        chosen = matched_member or default_member
        if chosen is not None and not explicit_ref:
            path = _member_audio(chosen)
            if path is not None:
                files["prompt_speech"] = (path.name, path.read_bytes(), "application/octet-stream")
                ref_info = {"source": "group", "rel": str(chosen.get("rel", "")),
                            "name": voice, "sha256": _sha256_file(path)[:16]}
                report["voice_resolved"] = f"{group.get('name', voice)} · {chosen.get('label', '')}" \
                    + ("（标签子串命中）" if matched_member is not None else "（默认成员）")
        # 情感：instructions → emo_text（内联/显式优先）
        if not explicit_emo:
            if overrides.get("emo_vector"):
                try:
                    kwargs["emo_vector"] = [float(x) for x in re.split(r"[，,]", overrides["emo_vector"]) if x.strip()]
                    report["emotion"] = {"source": "inline", "action": "vector"}
                except ValueError:
                    pass
            elif overrides.get("emo_text"):
                kwargs["use_emo_text"] = True
                kwargs["emo_text"] = overrides["emo_text"]
                report["emotion"] = {"source": "inline", "action": "text"}
            elif overrides.get("emo_ref"):
                roots = _load_library()
                rr = str(overrides["emo_ref"]).split("|", 1)
                if len(rr) == 2 and 0 <= int(rr[0] or 0) < len(roots):
                    gp = Path(roots[int(rr[0])]["path"]) / rr[1]
                    if gp.is_file():
                        kwargs["emo_prompt_speech_b64"] = base64.b64encode(gp.read_bytes()).decode("ascii")
                        report["emotion"] = {"source": "inline", "action": "audio"}
            elif instructions:
                kwargs["use_emo_text"] = True
                kwargs["emo_text"] = instructions
                report["emotion"] = {"source": "instructions", "action": "text"}
        data["input"] = (text.strip() or str(data.get("input") or "").strip())
        report["residual"] = data["input"]
        report["tag"] = ""
        kwargs.setdefault("language", "ZH")
        data["kwargs"] = json.dumps(kwargs, ensure_ascii=False)
        if not files.get("prompt_speech"):
            return JSONResponse(
                {"detail": "OpenAI 适配：voice 未命中音色分组且未上传 prompt_speech，无法合成", "report": report},
                status_code=400)
        return await _proxy_tts(data, files, ref_info, adapter_report=report)

    # ── 3) 分支：guide_audio / group_switch（#37 语义保持） ──
    # 情绪词来源：instructions 剥壳 + voice 段命中映射短语 + 内联情绪标签
    tag = overrides.get("tag", "")
    if instructions:
        m2 = DSH_INSTRUCTION_RE.match(instructions)
        word = m2.group(1) if m2 else instructions
        report["instruction"] = word
        tag = tag or word
        mi = DSH_INSTRUCTION_RE.match(text)
        if mi and mi.group(1) == word:
            text = text[mi.end():]
            report["stripped"].append({"kind": "instruction-echo", "value": word})
    voice_parts = [seg.strip() for seg in re.split(r"[，,]", voice)
                   if seg.strip() and seg.strip() != "default"]
    group = None
    for seg in voice_parts:
        g = next((g for g in groups_all if g.get("name") == seg), None)
        if g is not None:
            group = g
        else:
            hit = _match_emotion_rule(rules_for_adapter(), seg)
            if hit is not None and not tag:
                tag = hit.get("tag") or seg
                report["stripped"].append({"kind": "voice-emotion", "value": seg})
    if overrides.get("voice_group"):
        group = next((g for g in groups_all if g.get("name") == overrides["voice_group"]), None)
    hit = _match_emotion_rule(rules_for_adapter(), text, prefer_tag=tag)
    if hit is not None:
        for p in sorted(hit.get("phrases", []), key=len, reverse=True):
            if p and p in text:
                text = text.replace(p, "")
                report["stripped"].append({"kind": "phrase", "value": p})
        if not tag:
            tag = hit.get("tag") or tag
            report["tag"] = tag
    data["input"] = text.strip() or str(data.get("input") or "").strip()
    report["residual"] = data["input"]

    members = (group or {}).get("members", [])
    default_member = next((mm for mm in members if mm.get("is_default")), None) \
        or (members[0] if members else None)
    tagged_member = next((mm for mm in members
                          if str(mm.get("label", "")).lower() == tag.lower()), None)

    if not explicit_emo and mode == "guide_audio":
        guide_path = None
        if overrides.get("emo_ref"):
            roots = _load_library()
            rr = str(overrides["emo_ref"]).split("|", 1)
            if len(rr) == 2 and 0 <= int(rr[0] or 0) < len(roots):
                gp = Path(roots[int(rr[0])]["path"]) / rr[1]
                guide_path = gp if gp.is_file() else None
        elif tagged_member is not None:
            guide_path = _member_audio(tagged_member)
        elif hit is not None and hit.get("action") == "audio":
            roots = _load_library()
            rr = str(hit.get("audio", "")).split("|", 1)
            if len(rr) == 2 and 0 <= int(rr[0] or 0) < len(roots):
                gp = Path(roots[int(rr[0])]["path"]) / rr[1]
                guide_path = gp if gp.is_file() else None
        if guide_path is not None:
            kwargs["emo_prompt_speech_b64"] = base64.b64encode(guide_path.read_bytes()).decode("ascii")
            report["emotion"] = {"source": "guide_audio", "audio": guide_path.name}
        elif hit is not None and hit.get("action") in ("vector", "text") \
                and not any(k in kwargs for k in ("emo_vector", "use_emo_text")):
            if hit["action"] == "vector":
                kwargs["emo_vector"] = hit.get("vector") or [0, 0, 0, 0, 0, 0, 0, 0]
            else:
                kwargs["use_emo_text"] = True
                kwargs["emo_text"] = hit.get("text", "")
            kwargs.setdefault("emo_alpha", hit.get("alpha", 1.0))
            report["emotion"] = {"source": "mapping", "action": hit["action"]}
    elif not explicit_emo and mode == "group_switch" and hit is not None \
            and hit.get("action") in ("vector", "text") \
            and tagged_member is None \
            and not any(k in kwargs for k in ("emo_vector", "use_emo_text")):
        if hit["action"] == "vector":
            kwargs["emo_vector"] = hit.get("vector") or [0, 0, 0, 0, 0, 0, 0, 0]
        else:
            kwargs["use_emo_text"] = True
            kwargs["emo_text"] = hit.get("text", "")
        kwargs.setdefault("emo_alpha", hit.get("alpha", 1.0))
        report["emotion"] = {"source": "mapping", "action": hit["action"]}

    if not explicit_ref:
        chosen = tagged_member if (mode == "group_switch" and tagged_member is not None) else default_member
        if chosen is not None:
            path = _member_audio(chosen)
            if path is not None:
                files["prompt_speech"] = (path.name, path.read_bytes(), "application/octet-stream")
                ref_info = {"source": "group", "rel": str(chosen.get("rel", "")),
                            "name": voice, "sha256": _sha256_file(path)[:16]}
                report["voice_resolved"] = f"{group.get('name', voice)} · {chosen.get('label', '')}"
    kwargs.setdefault("language", "ZH")
    data["kwargs"] = json.dumps(kwargs, ensure_ascii=False)

    if not files.get("prompt_speech"):
        return JSONResponse(
            {"detail": "OpenAI 适配：voice 未命中音色分组且未上传 prompt_speech，无法合成", "report": report},
            status_code=400)
    return await _proxy_tts(data, files, ref_info, adapter_report=report)


def rules_for_adapter():
    return api_emotions_get().get("rules", [])


def _voice_seg_is_group(seg: str) -> bool:
    return any(g.get("name") == seg for g in _load_config("groups", {"groups": []}).get("groups", []))


def _match_emotion_rule(rules, text: str, prefer_tag: str = ""):
    """短语最长匹配（对正文）+ 联动标签/情绪词对齐；返回第一条命中规则或 None。"""
    for r in sorted(rules, key=lambda r: max([len(p) for p in r.get("phrases", [])] or [0]), reverse=True):
        for p in sorted(r.get("phrases", []), key=len, reverse=True):
            if p and (p in text or (prefer_tag and p == prefer_tag)):
                return r
        if prefer_tag and (r.get("tag") or "").lower() == prefer_tag.lower():
            return r
    return None


def _prune_history():
    dirs = sorted((d for d in HISTORY_DIR.iterdir() if d.is_dir()), key=lambda d: d.name)
    while len(dirs) > HISTORY_KEEP:
        shutil.rmtree(dirs.pop(0), ignore_errors=True)


# ────────────────────────── 请求历史 ──────────────────────────

def _read_meta(hdir: Path):
    try:
        return json.loads((hdir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"id": hdir.name, "status": -1, "input": "(meta 缺失)"}


@app.get("/api/history")
def api_history(limit: int = 100):
    dirs = sorted((d for d in HISTORY_DIR.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    items = []
    for hdir in dirs[: max(1, min(limit, HISTORY_KEEP))]:
        meta = _read_meta(hdir)
        meta["files"] = sorted(p.name for p in hdir.iterdir() if p.is_file() and p.name != "meta.json")
        items.append(meta)
    return {"total": len(dirs), "items": items}


def _safe_history_path(hid: str, name: str):
    parts = hid.split("-")
    if not (1 <= len(parts) <= 3 and all(p.isalnum() for p in parts)):
        return None
    hdir = (HISTORY_DIR / hid).resolve()
    try:
        hdir.relative_to(HISTORY_DIR.resolve())
    except (ValueError, OSError):
        return None
    path = (hdir / name).resolve()
    try:
        path.relative_to(hdir)
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


@app.get("/api/history/file")
def api_history_file(id: str, name: str):
    path = _safe_history_path(id, name)
    if path is None:
        return JSONResponse({"detail": "非法路径或文件不存在"}, status_code=404)
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@app.delete("/api/history/{hid}")
def api_history_delete(hid: str):
    hdir = (HISTORY_DIR / hid).resolve()
    try:
        hdir.relative_to(HISTORY_DIR.resolve())
    except (ValueError, OSError):
        return JSONResponse({"detail": "非法 id"}, status_code=400)
    if hdir.is_dir():
        shutil.rmtree(hdir, ignore_errors=True)
        logger.info("HISTORY DELETE %s", hid)
        return {"deleted": hid}
    return JSONResponse({"detail": "不存在"}, status_code=404)


@app.post("/api/history/clear")
def api_history_clear():
    n = 0
    for d in HISTORY_DIR.iterdir():
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    logger.info("HISTORY CLEAR (%d)", n)
    return {"cleared": n}


# ────────────────────────── 缓存管理 ──────────────────────────

@app.get("/api/cache/stats")
def api_cache_stats():
    audio = [p for p in CACHE_DIR.iterdir() if p.suffix != ".json"]
    return {"entries": len(audio), "bytes": sum(p.stat().st_size for p in audio),
            "max_entries": CACHE_MAX_ENTRIES, "max_bytes": CACHE_MAX_BYTES}


@app.post("/api/cache/clear")
def api_cache_clear():
    n = 0
    for p in CACHE_DIR.iterdir():
        p.unlink(missing_ok=True)
        n += 1
    logger.info("CACHE CLEAR (%d)", n)
    return {"cleared": n}


# ────────────────────────── 参考音频库（多根可配置） ──────────────────────────

def _load_library():
    try:
        cfg = json.loads(LIB_FILE.read_text(encoding="utf-8"))
        roots = cfg.get("roots")
        if isinstance(roots, list) and roots:
            return roots
    except (OSError, json.JSONDecodeError):
        pass
    roots = [{"name": "默认音色库", "path": REF_AUDIO_LIB or ""}]
    _save_library(roots)
    return roots


def _save_library(roots):
    LIB_FILE.write_text(json.dumps({"roots": roots}, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/library")
def api_library():
    roots = _load_library()
    return {"roots": [{**r, "exists": bool(r.get("path")) and Path(r["path"]).is_dir()} for r in roots]}


@app.post("/api/library")
async def api_library_save(request: Request):
    body = await request.json()
    roots = []
    for r in body.get("roots", []):
        name = str(r.get("name", "")).strip() or "未命名库"
        path = str(r.get("path", "")).strip()
        if path:
            roots.append({"name": name, "path": path})
    _save_library(roots)
    logger.info("LIBRARY SAVED roots=%d", len(roots))
    return api_library()


@app.get("/api/reflib")
def api_reflib():
    roots = _load_library()
    items, missing = [], []
    for idx, r in enumerate(roots):
        root_path = Path(r["path"])
        if not root_path.is_dir():
            missing.append(r["name"])
            continue
        for p in sorted(root_path.rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                rel = p.relative_to(root_path).as_posix()
                sub = Path(rel).parent.as_posix()
                items.append({
                    "root": idx,
                    "rel": rel,
                    "group": f"{r['name']}/{sub}" if sub != "." else r["name"],
                    "name": p.name,
                    "size_kb": round(p.stat().st_size / 1024),
                })
    return {"roots": [{"index": i, "name": r["name"], "path": r["path"],
                       "exists": Path(r["path"]).is_dir()} for i, r in enumerate(roots)],
            "missing": missing, "total": len(items), "items": items}


@app.get("/api/reflib/file")
def api_reflib_file(root: int, rel: str):
    roots = _load_library()
    if not 0 <= root < len(roots):
        return JSONResponse({"detail": "非法库源"}, status_code=404)
    root_path = Path(roots[root]["path"])
    if not root_path.is_dir():
        return JSONResponse({"detail": "库源目录不存在"}, status_code=404)
    try:
        resolved = (root_path / rel).resolve()
        resolved.relative_to(root_path.resolve())
    except (ValueError, OSError):
        return JSONResponse({"detail": "非法路径"}, status_code=400)
    if not resolved.is_file():
        return JSONResponse({"detail": "文件不存在"}, status_code=404)
    return FileResponse(resolved, media_type="application/octet-stream", filename=resolved.name)


# ────────────────────────── 配置文件（默认参数 / 情绪映射 / 音色分组） ──────────────────────────

CONFIG_FILES = {
    "settings": CONSOLE_DIR / "settings.json",        # API 默认参数（API 配置页）
    "ttsform": CONSOLE_DIR / "tts_defaults.json",     # 语音合成页默认填写状态（直接配置专用）
    "adapterform": CONSOLE_DIR / "adapter_defaults.json",  # OpenAI 适配页默认填写状态
    "emotions": CONSOLE_DIR / "emotion_map.json",     # 情绪映射规则（关键词→向量/文字/音频）
    "groups": CONSOLE_DIR / "voice_groups.json",      # 音色分组（同角色多情绪参考音频）
}


def _load_config(name: str, default):
    path = CONFIG_FILES[name]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.write_text(json.dumps(default, ensure_ascii=False, indent=1), encoding="utf-8")
        return default


def _save_config(name: str, value):
    CONFIG_FILES[name].write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("CONFIG SAVED %s", name)


@app.get("/api/settings")
def api_settings_get():
    value = _load_config("settings", {
        "lang": "ZH", "speed": 1.0, "response_format": "wav", "emo_alpha": 1.0,
        "parse_instruction": True, "adapter_mode": "text_emotion",
    })
    # 迁移：v1 的 auto_emotion（关键词自动匹配，已废弃）→ parse_instruction（DSHT 情绪指令解析）
    if "parse_instruction" not in value and "auto_emotion" in value:
        value["parse_instruction"] = bool(value.pop("auto_emotion"))
    return value


@app.post("/api/settings")
async def api_settings_save(request: Request):
    body = await request.json()
    value = _load_config("settings", {})
    for k in ("lang", "speed", "response_format", "emo_alpha", "parse_instruction",
              "auto_emotion", "adapter_mode"):
        if k in body:
            value[k] = body[k]
    if value.get("adapter_mode") not in ("text_emotion", "guide_audio", "group_switch", None, ""):
        value["adapter_mode"] = "text_emotion"
    if "auto_emotion" in value:  # 保存时完成迁移
        value["parse_instruction"] = bool(value.pop("auto_emotion"))
    _save_config("settings", value)
    return value


@app.get("/api/ttsform")
def api_ttsform_get():
    return _load_config("ttsform", {
        "lang": "ZH", "speed": 1.0, "response_format": "wav", "emo_alpha": 1.0,
        "auto_emotion": True, "emo_mode": "none",
    })


@app.post("/api/ttsform")
async def api_ttsform_save(request: Request):
    body = await request.json()
    value = _load_config("ttsform", {})
    for k in ("lang", "speed", "response_format", "emo_alpha", "auto_emotion", "emo_mode",
              "input", "ref_mode", "ref_lib", "ref_group", "ref_group_member",
              "emo_audio_lib", "emo_rule"):
        if k in body:
            value[k] = body[k]
    if value.get("emo_mode") not in ("none", "auto", "vector", "text", "audio"):
        value["emo_mode"] = "none"
    _save_config("ttsform", value)
    return value


@app.get("/api/adapterform")
def api_adapterform_get():
    return _load_config("adapterform", {
        "input": "", "voice": "default", "response_format": "wav",
        "speed": 1.0, "kwargs": "",
    })


@app.post("/api/adapterform")
async def api_adapterform_save(request: Request):
    body = await request.json()
    value = _load_config("adapterform", {})
    for k in ("input", "voice", "response_format", "speed", "kwargs"):
        if k in body:
            value[k] = body[k]
    _save_config("adapterform", value)
    return value


@app.get("/api/emotions")
def api_emotions_get():
    return _migrate_emotions(_load_config("emotions", {"rules": []}))


def _migrate_emotions(value):
    """v1 规则（name/keywords/…）→ v2（phrases/tag/action），对齐 OmniVoice 语义。"""
    for r in value.get("rules", []):
        if "keywords" in r:
            r["phrases"] = r.pop("keywords")
        if "name" in r and "tag" not in r:
            r["tag"] = r.pop("name")
        r.setdefault("action", "none")
    return value


@app.post("/api/emotions")
async def api_emotions_save(request: Request):
    body = await request.json()
    rules = body.get("rules", [])
    for r in rules:
        r["phrases"] = [p.strip() for p in r.get("phrases", []) if str(p).strip()]
        r["tag"] = str(r.get("tag", "")).strip()
        if r.get("action") not in ("none", "vector", "text", "audio"):
            r["action"] = "none"
        r["audio"] = str(r.get("audio", "")).strip()  # 引导音频引用 "root|rel"（参考音频库）
        if r["action"] == "vector" and not isinstance(r.get("vector"), list):
            r["vector"] = [0, 0, 0, 0, 0, 0, 0, 0]
        if r["action"] == "text":
            r["text"] = str(r.get("text", "")).strip()
        r["alpha"] = min(1.0, max(0.0, float(r.get("alpha", 1.0))))
    value = {"rules": rules}
    _save_config("emotions", value)
    return value


@app.get("/api/groups")
def api_groups_get():
    return _load_config("groups", {"groups": []})


@app.post("/api/groups")
async def api_groups_save(request: Request):
    body = await request.json()
    groups = body.get("groups", [])
    for g in groups:
        g["name"] = str(g.get("name", "")).strip()
        for m in g.get("members", []):
            m["label"] = str(m.get("label", "")).strip()
            m["root"] = int(m.get("root", 0))
            m["rel"] = str(m.get("rel", "")).strip()
    value = {"groups": groups}
    _save_config("groups", value)
    return value


def main():
    import uvicorn
    logger.info("console starting: port=%d endpoint=%s model=%s",
                CONSOLE_PORT, XINFERENCE_ENDPOINT, MODEL_NAME)
    uvicorn.run(app, host="127.0.0.1", port=CONSOLE_PORT, log_level="warning")


if __name__ == "__main__":
    main()
