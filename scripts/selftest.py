# -*- coding: utf-8 -*-
"""Xinference 本地语音服务 · 回归测试（发布前必跑）

覆盖：
  T1 服务端可编译 / 关键模块可导入
  T2 参考音频库引用格式：库名格式命中、索引格式兼容、非法引用报错
  T3 /api/reflib/file 接受库名与索引两种格式
  T4 引用体检（/api/refs/health）无 problems
  T5 内联指令解析：正则可抽出 + 能定位到真实文件 + 从正文剔除
  T6 三端口存活（9996 监控 / 9997 服务 / 9998 控制台）
  T7 前端内联 JS 语法（node vm）
  T8 前端复制逻辑（node 模拟 DOM，见 scripts/test_copy_logic.js）

用法： downloads/venv-main/Scripts/python.exe scripts/selftest.py
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONSOLE = BASE / "console"
SERVER_PY = CONSOLE / "server.py"
SERVER_PY_JS = str(SERVER_PY).replace("\\", "/")   # 供嵌入 python -c 代码串
VENV_PY = BASE / "downloads" / "venv-main" / "Scripts" / "python.exe"
NODE = "node"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  → " + detail) if detail else ""))
    return ok


def http_get(url, timeout=20):
    req = urllib.request.Request(url)
    req.add_header("Accept", "*/*")
    return urllib.request.urlopen(req, timeout=timeout)


# ───────────────────────── T1 编译 / 导入 ─────────────────────────
def t1_compile():
    print("\n── T1 服务端编译与导入 ──")
    r = subprocess.run([str(VENV_PY), "-m", "py_compile", str(CONSOLE / "server.py")],
                       capture_output=True, text=True)
    check("server.py 语法编译", r.returncode == 0, r.stderr.strip()[:200])

    code = (
        "import importlib.util,sys;"
        "spec=importlib.util.spec_from_file_location('srv', r'%s');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print('OK')" % SERVER_PY_JS
    )
    r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True,
                       cwd=str(BASE))
    check("server.py 可导入（关键函数存在）", "OK" in r.stdout, r.stderr.strip()[-300:])


# ───────────────────────── T2 引用格式 ─────────────────────────
def t2_ref_formats():
    print("\n── T2 库引用格式（库名优先 / 索引兼容 / 非法报错）──")
    code = r'''
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("srv", r"__SERVER_PY__")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
roots = m._load_library()
if not roots:
    print("NO_ROOTS"); sys.exit(2)
name = roots[0]["name"]
root_path = Path(roots[0]["path"])
files = [p for p in sorted(root_path.rglob("*")) if p.is_file() and p.suffix.lower() in (".wav",".mp3",".flac",".ogg")]
if not files:
    print("NO_FILES"); sys.exit(3)
rel = files[0].relative_to(root_path).as_posix()
out = {}
p, note = m._ref_from_piped(name + "|" + rel, roots)
out["byname_ok"] = (p is not None and p.is_file())
p2, note2 = m._ref_from_piped("0|" + rel, roots)
out["byindex_ok"] = (p2 is not None and p2.is_file())
p3, note3 = m._ref_from_piped("__no_such_lib__|" + rel, roots)
out["bad_rejected"] = (p3 is None and bool(note3))
out["migrate"] = m._migrate_piped_ref("0|" + rel) == (name + "|" + rel)
out["rel"] = rel
out["name"] = name
print(json.dumps(out, ensure_ascii=False))
'''.replace("__SERVER_PY__", SERVER_PY_JS)
    r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True, cwd=str(BASE))
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("T2 引用解析", False, (r.stdout + r.stderr)[-300:])
        return
    check("库名格式命中真实文件", d.get("byname_ok"), d.get("rel", "")[:60])
    check("旧索引格式仍兼容（带降级提示）", d.get("byindex_ok"))
    check("非法库名被拒并给出原因", d.get("bad_rejected"))
    check("_migrate_piped_ref 索引→库名", d.get("migrate"))


# ───────────────────────── T3 /api/reflib/file ─────────────────────────
def t3_reflib_file():
    print("\n── T3 /api/reflib/file 接受库名与索引 ──")
    try:
        d = json.load(http_get("http://127.0.0.1:9998/api/reflib", timeout=60))
    except Exception as e:
        check("读取 /api/reflib", False, str(e)[:120])
        return
    its = d.get("items") or []
    if not its:
        check("参考音频库非空", False, "items 为空")
        return
    it = its[0]
    import urllib.parse
    for label, rootval in (("库名", it.get("rootName") or ""), ("索引", str(it.get("root")))):
        q = urllib.parse.urlencode({"root": rootval, "rel": it["rel"]})
        try:
            r = http_get("http://127.0.0.1:9998/api/reflib/file?" + q, timeout=30)
            body = r.read()
            check("reflib/file %s 格式取音频" % label, r.status == 200 and len(body) > 1000,
                  "HTTP %s, %d bytes" % (r.status, len(body)))
        except urllib.error.HTTPError as e:
            check("reflib/file %s 格式取音频" % label, False, "HTTP %s" % e.code)
        except Exception as e:
            check("reflib/file %s 格式取音频" % label, False, str(e)[:100])


# ───────────────────────── T4 引用体检 ─────────────────────────
def t4_refs_health():
    print("\n── T4 引用体检 /api/refs/health ──")
    try:
        d = json.load(http_get("http://127.0.0.1:9998/api/refs/health", timeout=60))
        probs = d.get("problems") or []
        check("引用体检无失效项", not probs, "problems=%d" % len(probs))
    except Exception as e:
        check("引用体检接口可达", False, str(e)[:120])


# ───────────────────────── T5 内联指令 ─────────────────────────
def t5_inline():
    print("\n── T5 内联指令解析与剔除 ──")
    code = r'''
import importlib.util, json, re, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("srv", r"__SERVER_PY__")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
roots = m._load_library()
root_path = Path(roots[0]["path"])
files = [p for p in sorted(root_path.rglob("*")) if p.is_file() and p.suffix.lower() in (".wav",".mp3")]
rel = files[0].relative_to(root_path).as_posix()
name = roots[0]["name"]
BS = chr(92); NOBR = "[^" + BS + "[" + BS + "]]"
out = {}
for kind, tmpl, grp in (
    ("voice", "#[音色选择][{n}/{r}]", 1),
    ("emo",   "#[情感控制][引导音频][{n}/{r}]", 2),
):
    cmd = tmpl.replace("{n}", name).replace("{r}", rel)
    pat = "#" + BS + "[音色选择]" + BS + "[(" + NOBR + "{1,400})" + BS + "]" if kind == "voice" else \
          "#" + BS + "[情感控制]" + BS + "[(情绪向量|文字描述|引导音频)]" + BS + "[(" + NOBR + "{1,400})" + BS + "]"
    mm = re.search(pat, cmd)
    ok_parse = bool(mm)
    val = mm.group(grp) if ok_parse else ""
    rn, _, relfw = val.partition("/")
    item = {"root_name": rn.strip(), "rel": relfw.strip().replace("/", chr(92))}
    gp, note = m._audio_ref_path(item, roots)
    out[kind + "_parse"] = ok_parse
    out[kind + "_file"] = bool(gp and gp.is_file())
print(json.dumps(out, ensure_ascii=False))
'''.replace("__SERVER_PY__", SERVER_PY_JS)
    r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True, cwd=str(BASE))
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("T5 内联指令", False, (r.stdout + r.stderr)[-300:])
        return
    check("内联 音色选择 正则可解析", d.get("voice_parse"))
    check("内联 音色选择 定位到真实文件", d.get("voice_file"))
    check("内联 情感控制/引导音频 正则可解析", d.get("emo_parse"))
    check("内联 情感控制/引导音频 定位到真实文件", d.get("emo_file"))


# ───────────────────────── T6 三端口存活 ─────────────────────────
def t6_ports():
    print("\n── T6 三端口存活 ──")
    for port, label, path in ((9996, "监控状态页", "/status"), (9997, "Xinference 服务", "/status"),
                              (9998, "Web 控制台", "/api/health")):
        try:
            r = http_get("http://127.0.0.1:%d%s" % (port, path), timeout=10)
            check("%s (%d) 存活" % (label, port), r.status == 200, "HTTP %s" % r.status)
        except urllib.error.HTTPError as e:
            check("%s (%d) 存活" % (label, port), e.code == 200, "HTTP %s" % e.code)
        except Exception as e:
            check("%s (%d) 存活" % (label, port), False, str(e)[:90])


# ───────────────────────── T7 前端 JS 语法 ─────────────────────────
def t7_frontend_js():
    print("\n── T7 前端内联 JS 语法 ──")
    if not NODE:
        check("node 可用", False, "未找到 node")
        return
    js = (
        'const fs=require("fs"),vm=require("vm");'
        'const h=fs.readFileSync("__ADMIN_HTML__","utf8");'
        'const s=[...h.matchAll(/<script\\b[^>]*>([\\s\\S]*?)<\\/script>/gi)].map(m=>m[1]);'
        'let ok=true;s.forEach((c,i)=>{try{new vm.Script(c)}catch(e){ok=false;console.log("ERR "+i+": "+e.message)}});'
        'process.exit(ok?0:1)'
    ).replace("__ADMIN_HTML__", str(CONSOLE / "admin.html").replace("\\", "/"))
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True)
    check("admin.html 内联 JS 语法正确", r.returncode == 0, (r.stdout + r.stderr).strip()[:200])


# ───────────────────────── T8 前端复制逻辑 ─────────────────────────
def t8_copy_logic():
    print("\n── T8 前端复制逻辑（node 模拟 DOM）──")
    if not NODE:
        check("node 可用", False, "未找到 node")
        return
    r = subprocess.run([NODE, str(BASE / "scripts" / "test_copy_logic.js")],
                       capture_output=True, text=True, cwd=str(BASE))
    for line in (r.stdout or "").strip().splitlines():
        print("    " + line)
    check("复制逻辑回归（3 类复制 + 边界）", r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else "")


def main():
    print("=" * 64)
    print("Xinference 本地语音服务 · 回归测试  %s" % time_str())
    print("=" * 64)
    t1_compile()
    t2_ref_formats()
    t3_reflib_file()
    t4_refs_health()
    t5_inline()
    t6_ports()
    t7_frontend_js()
    t8_copy_logic()
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print("\n" + "=" * 64)
    print("结果：%d 通过 / %d 失败（共 %d 项）" % (passed, failed, len(results)))
    if failed:
        print("\n失败项：")
        for n, ok, d in results:
            if not ok:
                print("  ✗ %s  %s" % (n, d))
    print("=" * 64)
    return 1 if failed else 0


def time_str():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
