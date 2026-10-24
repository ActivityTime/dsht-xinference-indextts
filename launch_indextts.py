# -*- coding: utf-8 -*-
"""
在已运行的 Xinference 服务上加载 IndexTTS-2.5 (或 IndexTTS2 备选)。
- 通过 RESTfulClient 传 model_config={"use_bf16": ...} 以 bf16 加载, CLI 无法传此参数。
- 环境变量:
  XINFERENCE_ENDPOINT   端点, 默认 http://127.0.0.1:9997
  MODEL_NAME            模型名, 默认 IndexTTS-2.5
  USE_BF16              true/false, 默认 true
  SMALL_MODELS_DIR      离线 small_models 目录, 可选
  ALLOW_FALLBACK        1 时才在 IndexTTS-2.5 失败后回退 IndexTTS2(旧模型), 默认关
"""
import os
import sys

from xinference.client import RESTfulClient

endpoint = os.environ.get("XINFERENCE_ENDPOINT", "http://127.0.0.1:9997")
model_name = os.environ.get("MODEL_NAME", "IndexTTS-2.5")
use_bf16 = os.environ.get("USE_BF16", "true").strip().lower() not in ("0", "false", "no", "off")
small_dir = os.environ.get("SMALL_MODELS_DIR", "").strip()

client = RESTfulClient(endpoint)


def launch(name):
    config = {"use_bf16": use_bf16}
    if small_dir:
        config["small_models_dir"] = small_dir
    print(f"Launching {name} (use_bf16={use_bf16}) on {endpoint} ...")
    try:
        uid = client.launch_model(model_name=name, model_type="audio", model_config=config)
        print(f"OK model_uid={uid}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {e}")
        return False


ok = launch(model_name)
if not ok and model_name == "IndexTTS-2.5" and os.environ.get("ALLOW_FALLBACK", "0") == "1":
    print("Fallback to IndexTTS2 ...")
    ok = launch("IndexTTS2")

sys.exit(0 if ok else 1)
