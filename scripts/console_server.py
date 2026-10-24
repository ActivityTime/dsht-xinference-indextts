# -*- coding: utf-8 -*-
"""
IndexTTS-2.5 独立 Web 控制台（127.0.0.1:9998）。

- 页面：console/admin.html（单文件、无 CDN、样式对齐 Xinference 内置 WebUI 主题）。
- 代理：/api/tts -> Xinference /v1/audio/speech（multipart 透传）；/api/status -> /status。
- 音色库：只读列举并按流式返回 REF_AUDIO_LIB（config.ps1 注入的参考音频库目录）。

单机内网用途：只绑 127.0.0.1；音色库接口做了路径穿越防护（只允许库目录内的相对路径）。
端口被占用即退出（视为已有实例在运行），与启动脚本的 9999 互斥锁约定互不冲突。
"""
import json
import os
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

BASE_DIR = Path(__file__).resolve().parent.parent          # 项目根（K:\SomeApps\Xinference）
ADMIN_HTML = BASE_DIR / "console" / "admin.html"
XINFERENCE_ENDPOINT = os.environ.get("XINFERENCE_ENDPOINT", "http://127.0.0.1:9997").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "IndexTTS-2.5")
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "9998"))
REF_AUDIO_LIB = Path(os.environ.get("REF_AUDIO_LIB", "") or "")
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}

app = FastAPI(title="IndexTTS-2.5 Console", docs_url=None, redoc_url=None, openapi_url=None)


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
        "ref_audio_lib": str(REF_AUDIO_LIB) if REF_AUDIO_LIB.is_dir() else "（未配置或不存在）",
    }


@app.get("/api/status")
def api_status():
    try:
        r = requests.get(XINFERENCE_ENDPOINT + "/status", timeout=5)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"Xinference 不可达: {e}"}, status_code=502)


@app.post("/api/tts")
async def api_tts(request: Request):
    """multipart 透传到 Xinference /v1/audio/speech，音频字节原样回流。"""
    form = await request.form()
    data = {}
    files = {}
    for key, value in form.multi_items():
        if hasattr(value, "read"):  # UploadFile（prompt_speech / 未来扩展的情感参考音频）
            files[key] = (value.filename, await value.read(), value.content_type or "application/octet-stream")
        else:
            data[key] = value
    if not files.get("prompt_speech"):
        return JSONResponse({"detail": "缺少 prompt_speech（参考音频）"}, status_code=400)
    try:
        upstream = requests.post(
            XINFERENCE_ENDPOINT + "/v1/audio/speech", data=data, files=files, timeout=600)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"Xinference 不可达: {e}"}, status_code=502)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"))


def _safe_ref_path(rel: str) -> Path | None:
    """只允许 REF_AUDIO_LIB 目录内的相对路径（防路径穿越）。"""
    if not REF_AUDIO_LIB.is_dir():
        return None
    try:
        resolved = (REF_AUDIO_LIB / rel).resolve()
        resolved.relative_to(REF_AUDIO_LIB.resolve())
    except (ValueError, OSError):
        return None
    return resolved if resolved.is_file() else None


@app.get("/api/reflib")
def api_reflib():
    if not REF_AUDIO_LIB.is_dir():
        return {"root": str(REF_AUDIO_LIB), "total": 0, "items": [],
                "note": "REF_AUDIO_LIB 未配置或目录不存在（检查 config.ps1）"}
    items = []
    for p in sorted(REF_AUDIO_LIB.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            rel = p.relative_to(REF_AUDIO_LIB).as_posix()
            group = Path(rel).parent.as_posix()
            items.append({
                "rel": rel,
                "group": group if group != "." else "（根目录）",
                "name": p.name,
                "size_kb": round(p.stat().st_size / 1024),
            })
    return {"root": str(REF_AUDIO_LIB), "total": len(items), "items": items}


@app.get("/api/reflib/file")
def api_reflib_file(rel: str):
    path = _safe_ref_path(rel)
    if path is None:
        return JSONResponse({"detail": "非法路径或文件不存在"}, status_code=404)
    # 全量返回（参考音频 3-10s / 数百 KB，无需 Range 支持；<audio> 可整段播放）
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=CONSOLE_PORT, log_level="warning")


if __name__ == "__main__":
    main()
