# -*- coding: utf-8 -*-
"""
Xinference 本地语音服务 — 存活状态窗口 (#64)
==========================================
Tkinter 窗口：后台线程轮询 9997(Xinference 服务) / 9998(控制台) /v1/models，
以绿/红指示灯展示三路存活与 GPU 占用；提供 启动 / 停止 / 刷新 按钮。
依赖：标准库 tkinter（项目 venv 已含 8.6）。路径全部相对本脚本推导，无绝对路径。
用法：venv/Scripts/python.exe status_window.py
"""
import os
import sys
import time
import json
import threading
import subprocess
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT_XINF = int(os.environ.get("XINFERENCE_PORT", "9997"))
PORT_CONSOLE = int(os.environ.get("CONSOLE_PORT", "9998"))
ENDPOINT = "http://127.0.0.1:%d" % PORT_XINF
CONSOLE_URL = "http://127.0.0.1:%d" % PORT_CONSOLE
START_PS1 = os.path.join(BASE_DIR, "start_xinference_indextts.ps1")
STOP_PS1 = os.path.join(BASE_DIR, "stop_xinference_indextts.ps1")
POLL_INTERVAL = 3.0


def http_get_json(url, timeout=5):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def probe():
    """返回三路状态 dict（任一请求失败即视为该路不存活）。"""
    xinf_up = http_get_json(ENDPOINT + "/status") is not None
    models = http_get_json(ENDPOINT + "/v1/models")
    model_loaded = bool(models) and any(
        (m.get("model_name") in ("IndexTTS-2.5", "IndexTTS2") or m.get("id") == "IndexTTS-2.5")
        for m in (models or {}).get("data", []))
    console_up = http_get_json(CONSOLE_URL + "/api/status") is not None
    gpu = ""
    st = http_get_json(ENDPOINT + "/status")
    if st:
        workers = st.get("workers") or {}
        if workers:
            first = next(iter(workers.values()))
            gpu0 = (first.get("status") or {}).get("gpu-0") or {}
            if gpu0:
                gpu = "%.1f/%.1f GB (%.0f%%)" % (
                    gpu0["mem_used"] / 1073741824, gpu0["mem_total"] / 1073741824,
                    gpu0["mem_usage"] * 100)
    return {"xinf": xinf_up, "model": model_loaded, "console": console_up, "gpu": gpu}


class StatusWindow:
    def __init__(self, root):
        self.root = root
        self._stop = False
        root.title("Xinference 语音服务 · 存活状态")
        root.geometry("440x320")
        root.resizable(False, False)
        self.latest = {"xinf": False, "model": False, "console": False, "gpu": ""}
        self._build_ui()
        threading.Thread(target=self._bg_loop, daemon=True).start()

    def _dot(self, parent, text):
        f = ttk.Frame(parent)
        cv = tk.Canvas(f, width=14, height=14)
        lbl = ttk.Label(f, text=text)
        cv.pack(side="left", padx=(0, 6))
        lbl.pack(side="left")
        f.pack(fill="x", padx=14, pady=5)
        return cv, lbl

    def _build_ui(self):
        ttk.Label(self.root, text="Xinference + IndexTTS-2.5 本地语音服务",
                  font=("Segoe UI", 12, "bold")).pack(pady=(12, 4))
        self.cv_xinf, _ = self._dot(self.root, "Xinference 服务  (9997)")
        self.cv_model, _ = self._dot(self.root, "IndexTTS-2.5 模型")
        self.cv_console, _ = self._dot(self.root, "控制台  (9998)")
        self.gpu_var = tk.StringVar(value="GPU: —")
        ttk.Label(self.root, textvariable=self.gpu_var).pack(pady=(6, 2))
        self.note_var = tk.StringVar(value="每 %d 秒自动刷新" % int(POLL_INTERVAL))
        ttk.Label(self.root, textvariable=self.note_var, foreground="#888").pack()

        bf = ttk.Frame(self.root)
        bf.pack(pady=(16, 8))
        ttk.Button(bf, text="启动", command=self.on_start).pack(side="left", padx=8)
        ttk.Button(bf, text="停止", command=self.on_stop).pack(side="left", padx=8)
        ttk.Button(bf, text="刷新", command=self._bg_once).pack(side="left", padx=8)

    def _paint(self, cv, ok):
        cv.delete("all")
        cv.create_oval(2, 2, 12, 12, fill=("#2ecc71" if ok else "#e74c3c"), outline="")

    def _refresh(self):
        s = self.latest
        self._paint(self.cv_xinf, s["xinf"])
        self._paint(self.cv_model, s["model"])
        self._paint(self.cv_console, s["console"])
        self.gpu_var.set("GPU: " + (s["gpu"] or "—"))

    def _bg_once(self):
        threading.Thread(target=self._probe_store, daemon=True).start()

    def _probe_store(self):
        try:
            self.latest = probe()
        except Exception:
            pass
        self.root.after(0, self._refresh)

    def _bg_loop(self):
        while not self._stop:
            self._probe_store()
            time.sleep(POLL_INTERVAL)

    def _run_ps1(self, ps1, label):
        if not os.path.exists(ps1):
            messagebox.showerror("文件缺失", "找不到 %s" % ps1)
            return
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps1], creationflags=subprocess.CREATE_NO_WINDOW)
            self.note_var.set("%s 已触发，稍后查看状态" % label)
            self.root.after(4000, self._bg_once)
        except Exception as e:
            messagebox.showerror("操作失败", str(e))

    def on_start(self):
        self._run_ps1(START_PS1, "启动")

    def on_stop(self):
        if messagebox.askyesno("确认停止", "将关停 Xinference 服务、控制台与模型 worker，释放显存。继续？"):
            self._run_ps1(STOP_PS1, "停止")


def main():
    try:
        root = tk.Tk()
    except Exception as e:
        sys.stderr.write("Tkinter 不可用: %s\n" % e)
        sys.exit(1)
    StatusWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
