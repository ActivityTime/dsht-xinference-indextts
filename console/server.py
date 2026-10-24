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
import hashlib
import json
import logging
import logging.handlers
import os
import shutil
import time
from pathlib import Path

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
    return FileResponse(ADMIN_HTML, media_type="text/html; charset=utf-8")


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


@app.post("/api/tts")
async def api_tts(request: Request):
    """multipart 透传 + 同参数缓存 + 请求归档 + 日志。"""
    t0 = time.perf_counter()
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

    cacheable = bool(files.get("prompt_speech"))
    cache_key = None
    if cacheable:
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
        return Response(content=hit_path.read_bytes(), status_code=200,
                        media_type="application/octet-stream",
                        headers={"X-Cache": "hit", "X-Cache-Key": cache_key})

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
                "cache": "miss", "cache_key": cache_key, "audio": audio_name}
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
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/octet-stream"),
                    headers={"X-Cache": "miss", "X-Cache-Key": cache_key or ""})


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
    "ttsform": CONSOLE_DIR / "tts_defaults.json",     # 语音合成页默认填写状态（测试页专用）
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
    return _load_config("settings", {
        "lang": "ZH", "speed": 1.0, "response_format": "wav", "emo_alpha": 1.0,
        "auto_emotion": True,
    })


@app.post("/api/settings")
async def api_settings_save(request: Request):
    body = await request.json()
    value = _load_config("settings", {})
    for k in ("lang", "speed", "response_format", "emo_alpha", "auto_emotion"):
        if k in body:
            value[k] = body[k]
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
    if value.get("emo_mode") not in ("none", "vector", "text", "audio"):
        value["emo_mode"] = "none"
    _save_config("ttsform", value)
    return value


@app.get("/api/emotions")
def api_emotions_get():
    return _load_config("emotions", {"rules": []})


@app.post("/api/emotions")
async def api_emotions_save(request: Request):
    body = await request.json()
    rules = body.get("rules", [])
    for r in rules:
        r["name"] = str(r.get("name", "")).strip()
        r["keywords"] = [k.strip() for k in r.get("keywords", []) if str(k).strip()]
        if r.get("action") not in ("vector", "text"):
            r["action"] = "vector"
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
