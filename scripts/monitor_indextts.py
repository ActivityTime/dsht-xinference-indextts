# -*- coding: utf-8 -*-
"""
IndexTTS-2.5 守护监控脚本 (monitor_indextts.py)
================================================
职责（对应需求）：
  1) 单例守卫：本脚本自身只允许一个实例（锁文件）。
  2) 服务单例：确保 9997 端口只有唯一监听者（Xinference 服务单实例）。
  3) 模型单实例：通过 /v1/models 检查 IndexTTS-2.5 实例，>1 则自动卸载多余的，
     防止重复加载 / 多实例（对应 #16 双实例 bug）。
  4) 进程互监控：服务在但模型未加载 -> 触发加载；服务挂 -> 重启服务并加载。
  5) 周期循环执行并写日志。

用法：
  python monitor_indextts.py [--once] [--interval N]
    --once        只检查一轮后退出（供脚本调用）
    --interval N  轮询间隔秒（默认 20）

依赖：psutil、xinference client（均在主 venv）。所有路径/端口从环境变量读，无绝对路径。
"""
import os
import sys
import time
import socket
import subprocess
import argparse
import traceback

import psutil

# ---------------- 配置（从环境变量读，无绝对路径）----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = os.environ.get("XINFERENCE_ENDPOINT", "http://127.0.0.1:9997")
PORT = int(os.environ.get("XINFERENCE_PORT", "9997"))
MODEL = os.environ.get("MODEL_NAME", "IndexTTS-2.5")
LOCK_FILE = os.path.join(BASE_DIR, ".monitor.lock")
LOG_FILE = os.path.join(BASE_DIR, "logs", "monitor.log")
LAUNCH_SCRIPT = os.path.join(BASE_DIR, "scripts", "launch_indextts.py")
VENV_PY = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")
XINFERENCE_LOCAL = os.path.join(BASE_DIR, "venv", "Scripts", "xinference-local.exe")


def log(msg):
    """写日志（同时打控制台）。"""
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def acquire_lock():
    """用 PID 文件实现单例守卫：已存在且 PID 存活 => 视为已有实例返回 None。

    Windows 下 socket 锁受 SO_REUSEADDR 语义影响不可靠，改文件锁。
    返回 pidfile 路径；返回 None 表示已有实例在运行。
    """
    pidfile = os.path.join(BASE_DIR, ".monitor.pid")
    try:
        if os.path.exists(pidfile):
            with open(pidfile, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid > 0 and psutil.pid_exists(old_pid):
                return None  # 已有 monitor 实例存活
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
        return pidfile
    except Exception:
        return None


def release_lock(pidfile):
    """退出时清理 PID 文件。"""
    try:
        if pidfile and os.path.exists(pidfile):
            os.remove(pidfile)
    except Exception:
        pass


def _is_local_py(cmdline):
    return cmdline and "xinference-local" in cmdline


def port_listeners():
    """返回当前监听 9997 端口的进程 PID 列表。"""
    pids = set()
    for conn in psutil.net_connections(kind="tcp4"):
        if conn.status == "LISTEN" and conn.laddr.port == PORT:
            pids.add(conn.pid)
    return list(pids)


def service_ready():
    """探测 /status 是否 200。"""
    try:
        import urllib.request
        req = urllib.request.Request(ENDPOINT + "/status", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def list_indextts_instances():
    """返回 IndexTTS-2.5 的实例 uid 列表。

    RESTfulClient.list_models() 返回 {uid: 模型信息dict}，外层 key 即 model_uid。
    """
    try:
        from xinference.client import RESTfulClient
        c = RESTfulClient(ENDPOINT)
        models = c.list_models()
        insts = []
        if isinstance(models, dict):
            for uid, info in models.items():
                name = ""
                if isinstance(info, dict):
                    name = info.get("model_name") or info.get("id") or ""
                if name in (MODEL, "IndexTTS2"):  # 旧名也视为同一模型
                    insts.append(uid)
        return insts
    except Exception:
        log("list_models 调用失败: %s" % traceback.format_exc())
        return None  # None 表示查询失败，不贸然操作


def terminate_duplicates():
    """模型实例 >1 -> 卸载多余，仅保留一个。"""
    insts = list_indextts_instances()
    if insts is None:
        return
    if len(insts) > 1:
        keep, dups = insts[0], insts[1:]
        log("检测到 IndexTTS-2.5 多实例 %s，保留 %s，卸载 %s" % (insts, keep, dups))
        try:
            from xinference.client import RESTfulClient
            c = RESTfulClient(ENDPOINT)
            for uid in dups:
                try:
                    c.terminate_model(uid)
                    log("已卸载重复实例 %s" % uid)
                except Exception as e:
                    log("卸载 %s 失败: %s" % (uid, e))
        except Exception as e:
            log("terminate 失败: %s" % e)
    elif len(insts) == 0:
        log("IndexTTS-2.5 未加载（0 实例）")
    else:
        log("IndexTTS-2.5 实例数正常: %s" % insts)


def ensure_service_and_model():
    """服务/模型健康保障：服务挂则启服务；服务在则确保模型已加载。"""
    ready = service_ready()
    if not ready:
        log("服务未就绪，尝试启动 xinference-local ...")
        try:
            env = dict(os.environ)
            env.setdefault("XINFERENCE_HOME", os.path.join(BASE_DIR, ".xinference"))
            proc = subprocess.Popen(
                [VENV_PY, XINFERENCE_LOCAL, "--host", "127.0.0.1", "--port", str(PORT)],
                env=env, cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"),
            )
            log("xinference-local 已拉起 PID=%s" % proc.pid)
        except Exception as e:
            log("启动服务失败: %s" % e)
        # 等待就绪
        for _ in range(20):
            if service_ready():
                log("服务就绪")
                break
            time.sleep(3)
        if not service_ready():
            log("服务仍未就绪（继续尝试下一轮）")
            return
    # 服务在 -> 模型去重 + 确保加载
    terminate_duplicates()
    insts = list_indextts_instances()
    if insts is not None and len(insts) == 0:
        log("IndexTTS-2.5 未加载，触发加载 ...")
        try:
            env = dict(os.environ)
            env.setdefault("XINFERENCE_ENDPOINT", ENDPOINT)
            env.setdefault("MODEL_NAME", MODEL)
            subprocess.run([VENV_PY, LAUNCH_SCRIPT], env=env, cwd=BASE_DIR,
                           timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
            log("IndexTTS-2.5 加载命令已执行")
        except Exception as e:
            log("加载失败: %s" % e)


def check_service_singleton():
    """9997 端口多监听者 -> 保留一个，杀多余（防多服务实例）。"""
    listeners = port_listeners()
    if len(listeners) > 1:
        # 保留最老(最小 PID 或最先出现)的一个，杀其余
        keep = sorted(listeners)[0]
        log("检测到 %s 个服务进程监听 %s，保留 %s，终止其余 %s" % (len(listeners), PORT, keep, listeners[1:]))
        for pid in listeners[1:]:
            try:
                psutil.Process(pid).terminate()
                log("已终止重复服务进程 PID=%s" % pid)
            except Exception as e:
                log("终止 PID=%s 失败: %s" % (pid, e))
    elif len(listeners) == 1:
        log("服务单例正常: PID=%s 监听 %s" % (listeners[0], PORT))
    else:
        log("无进程监听 %s" % PORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只检查一轮")
    ap.add_argument("--interval", type=int, default=20, help="轮询间隔秒")
    args = ap.parse_args()

    lock = acquire_lock()
    if lock is None:
        log("已有监控实例在运行，本实例退出（单例守卫）")
        sys.exit(0)
    log("IndexTTS 守护监控启动 ENDPOINT=%s PID=%s" % (ENDPOINT, os.getpid()))

    if args.once:
        check_service_singleton()
        ensure_service_and_model()
    else:
        while True:
            try:
                check_service_singleton()
                ensure_service_and_model()
            except Exception as e:
                log("监控循环异常: %s" % traceback.format_exc())
            time.sleep(args.interval)
    release_lock(lock)


if __name__ == "__main__":
    main()
