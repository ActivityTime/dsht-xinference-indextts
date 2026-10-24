# -*- coding: utf-8 -*-
"""
IndexTTS-2.5 语音合成测试客户端 (Xinference /v1/audio/speech multipart)
用法:
  python tts_test.py <参考音频> <文本> [输出wav] [语言] [emo_vector JSON] [use_emo_text 0/1]
示例:
  python tts_test.py ref.wav "こんにちは" out.wav JA
  python tts_test.py ref.wav "我很开心!" out.wav ZH "[0,1,0,0,0,0,0,0]"
"""
import json
import os
import sys

import requests

# 端点/模型名从环境变量读取（由 config.ps1 注入；禁止硬编码绝对路径）
ENDPOINT = os.environ.get("XINFERENCE_ENDPOINT", "http://127.0.0.1:9997")
MODEL    = os.environ.get("MODEL_NAME", "IndexTTS-2.5")

ref = sys.argv[1] if len(sys.argv) > 1 else None
text = sys.argv[2] if len(sys.argv) > 2 else "テストです。"
out = sys.argv[3] if len(sys.argv) > 3 else "out.wav"
lang = (sys.argv[4] if len(sys.argv) > 4 else "JA").upper()
emo_vec = json.loads(sys.argv[5]) if len(sys.argv) > 5 else None
use_emo_text = sys.argv[6] == "1" if len(sys.argv) > 6 else False

if not ref:
    raise SystemExit("需要参考音频路径")

kwargs = {"language": lang}
if emo_vec:
    kwargs["emo_vector"] = emo_vec
if use_emo_text:
    kwargs["use_emo_text"] = True
    kwargs["emo_text"] = "joyful and excited"

data = {
    "model": MODEL,
    "input": text,
    "voice": "default",
    "response_format": "wav",
    "stream": "false",
    "speed": "1.0",
    "kwargs": json.dumps(kwargs, ensure_ascii=False),
}
f = open(ref, "rb")
try:
    files = {"prompt_speech": (ref, f, "application/octet-stream")}

    print(f"POST {ENDPOINT}/v1/audio/speech  kwargs={kwargs}")
    r = requests.post(ENDPOINT + "/v1/audio/speech", files=files, data=data, timeout=180)
finally:
    f.close()
print(f"HTTP {r.status_code}  content-type={r.headers.get('content-type')}")
if r.status_code == 200:
    with open(out, "wb") as f:
        f.write(r.content)
    print(f"OK saved {len(r.content)} bytes -> {out}")
else:
    print(r.text[:2000])
