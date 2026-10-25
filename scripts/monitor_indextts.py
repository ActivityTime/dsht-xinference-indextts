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
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import msvcrt  # Windows 排他文件锁（竞逸安全的单例守卫）
except Exception:  # 非 Windows 兜底
    msvcrt = None

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
# 控制台（9998）自愈参数：从已注入环境变量读，无则取默认值；路径从 BASE_DIR 推导，无绝对路径。
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "9998"))
CONSOLE_SCRIPT = os.path.join(BASE_DIR, "console", "server.py")
REF_AUDIO_LIB = os.environ.get("REF_AUDIO_LIB", "")


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


_LOCK_FD = None


def _is_monitor_proc(pid):
    """判断 PID 是否本监控进程（按命令行特征，防 PID 复用误判）。"""
    try:
        cl = psutil.Process(pid).cmdline()
        return bool(cl) and any("monitor_indextts.py" in c for c in cl)
    except Exception:
        return False


def acquire_lock():
    """单例守卫：msvcrt 排他文件锁（.monitor.lock，无竞态）+ .monitor.pid 信息文件。

    旧纯 PID 文件法有竞态（两个实例同时读到陈旧/空文件都通过）；改排他锁从根本上禁止。
    返回 lock 路径；返回 None 表示已有实例持锁，本实例应退出。
    """
    global _LOCK_FD
    try:
        fd = open(LOCK_FILE, "a+")
    except Exception:
        return None
    if msvcrt is not None:
        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fd.close()
            return None  # 已被其它 monitor 持有排他锁
    _LOCK_FD = fd  # 进程生命周期内持有；异常退出由 OS 释放
    try:
        pidfile = os.path.join(BASE_DIR, ".monitor.pid")
        old = 0
        if os.path.exists(pidfile):
            with open(pidfile) as f:
                old = int((f.read() or "0").strip() or 0)
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
        if old and old != os.getpid() and _is_monitor_proc(old):
            log("警告: 发现另一 monitor PID=%s 仍在运行（排他锁应已阻止其二重启动，若并存请人工核查）" % old)
    except Exception:
        pass
    return LOCK_FILE


def release_lock(lockpath):
    """退出时释放排他锁、关闭句柄、清理自己的 .monitor.pid。"""
    global _LOCK_FD
    try:
        if _LOCK_FD is not None:
            if msvcrt is not None:
                try:
                    _LOCK_FD.seek(0)
                    msvcrt.locking(_LOCK_FD.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            _LOCK_FD.close()
            _LOCK_FD = None
    except Exception:
        pass
    try:
        pidfile = os.path.join(BASE_DIR, ".monitor.pid")
        if os.path.exists(pidfile):
            with open(pidfile) as f:
                if (f.read() or "").strip() == str(os.getpid()):
                    os.remove(pidfile)
    except Exception:
        pass


def _is_local_py(cmdline):
    return cmdline and "xinference-local" in cmdline


def listener_pids(port):
    """返回监听指定端口（tcp4 LISTEN）的进程 PID 列表。"""
    pids = set()
    for conn in psutil.net_connections(kind="tcp4"):
        if conn.status == "LISTEN" and conn.laddr.port == port:
            pids.add(conn.pid)
    return sorted(p for p in pids if p)


def port_listeners():
    """返回当前监听 9997 端口的进程 PID 列表。"""
    return listener_pids(PORT)


def count_procs(sig):
    """按命令行子串特征统计进程实例，返回 [(pid, mem_mb, cmdline)]。

    用于「禁止多实例」：不仅看谁绑了端口，没绑上的游离体也要数出来杀掉。
    """
    out = []
    me = os.getpid()
    sig_n = sig.replace("/", "\\")
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            if p.info["pid"] == me:
                continue
            cl = p.info.get("cmdline") or []
            joined = " ".join(cl).replace("/", "\\")
            if sig_n in joined:
                mem = 0
                mi = p.info.get("memory_info")
                if mi:
                    mem = int(mi.rss / 1048576)
                out.append((p.info["pid"], mem, joined))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _count_instances(procs):
    """实例数 = 把 matched 进程按"父进程不在本集合"聚成的树根个数（不是进程个数）。

    每个组件(xinference/console/xoscar)都是 supervisor→worker 一对进程，实为 1 个实例；
    按进程个数数会把单实例误报成多实例（主管+工人=2）。按树根数才准。
    """
    pids = {p[0] for p in procs}
    roots = 0
    for pid, _, _ in procs:
        try:
            ppid = psutil.Process(pid).ppid()
        except Exception:
            ppid = None
        if ppid not in pids:
            roots += 1
    return roots


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
    """服务/模型健康保障。

    🔴 禁多实例（根因修复）：起 xinf 的唯一下界是「9997 无人监听」。只要 9997 有监听者，
    就说明服务在跑或正在启动/加载中，**绝不重复起**。（旧逻辑按 service_ready 判，模型加载
    那 60-90s /status 未就绪 → 被反复抢起 → 多个 xinf/多套池，显存爆，正是病根。）
    """
    listeners = listener_pids(PORT)
    if not listeners:
        log("9997 无监听，启动 xinference-local ...")
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
        for _ in range(40):
            if service_ready():
                log("服务就绪")
                break
            time.sleep(3)
        if not service_ready():
            log("服务仍未就绪（下一轮继续）")
            return
    else:
        # 9997 有监听者 = 服务在；未就绪则是在启动/加载中，绝不重复起
        if not service_ready():
            log("9997 有监听者(PID=%s)但未就绪，判定启动/加载中，本轮不重复拉起" % listeners)
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


def console_ready():
    """探测控制台 9998 是否真正响应（HTTP，而非裸 TCP connect）。

    裸 TCP 对「端口在 LISTEN 但事件循环被阻塞」的僵死控制台也返回连接成功，
    会误判存活、不触发自愈；改 HTTP 探测 /api/health 才能识别僵死并重启。
    """
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/api/health" % CONSOLE_PORT, method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_console():
    """控制台单例 + 自愈：9998 无 HTTP 响应且无进程 -> 拉起；有进程但未就绪 -> 判启动中不重复拉。

    控制台是静态页 + 代理，秒起、不占显存；但此前无人看守，死了不会自起
    （#64：互为孤儿）。此处补齐它的监护人，并禁多实例。
    """
    if console_ready():
        return
    existing = count_procs(os.path.join("console", "server.py"))
    if existing:
        log("控制台进程已存在但未就绪(PID=%s)，判定启动中，本轮不重复拉起" %
            [p[0] for p in existing])
        return
    log("控制台未存活且无进程，尝试启动 console/server.py ...")
    try:
        env = dict(os.environ)
        env.setdefault("XINFERENCE_ENDPOINT", ENDPOINT)
        env.setdefault("MODEL_NAME", MODEL)
        env.setdefault("CONSOLE_PORT", str(CONSOLE_PORT))
        env.setdefault("REF_AUDIO_LIB", REF_AUDIO_LIB)
        proc = subprocess.Popen(
            [VENV_PY, CONSOLE_SCRIPT], env=env, cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"),
        )
        log("控制台已拉起 PID=%s" % proc.pid)
    except Exception as e:
        log("启动控制台失败: %s" % e)


def _kill(pid, why):
    """坚决杀掉：先 terminate，2s 不退就 SIGKILL。"""
    try:
        p = psutil.Process(pid)
        p.terminate()
        try:
            p.wait(timeout=2)
        except psutil.TimeoutExpired:
            p.kill()
            try:
                p.wait(timeout=2)
            except Exception:
                pass
        log("已击杀 %s PID=%s" % (why, pid))
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception as e:
        # terminate 失败（拒达）时兜底再强杀一次
        try:
            psutil.Process(pid).kill()
            log("已强杀 %s PID=%s（terminate 受阻后 kill）" % (why, pid))
            return True
        except Exception as e2:
            log("杀 PID=%s 失败: %s / %s" % (pid, e, e2))
            return False


def _descendants(root_pids):
    """返回 root_pids 的全部后代 PID 集合（递归子进程）。"""
    seen = set()
    stack = [p for p in root_pids if p]
    while stack:
        pid = stack.pop()
        try:
            for ch in psutil.Process(pid).children(recursive=False):
                if ch.pid not in seen:
                    seen.add(ch.pid)
                    stack.append(ch.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return seen


def _ancestors(pid):
    """返回 pid 的全部祖先 PID 集合（向上追溯父链）。"""
    out = set()
    try:
        p = psutil.Process(pid)
        while True:
            par = p.parent()
            if par is None:
                break
            out.add(par.pid)
            p = par
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return out


def _instance_tree(pid):
    """返回包含 pid 的整棵进程树（最顶层祖先 → 所有后代 + 中间祖先）。

    xinference/xoscar 是进程树（supervisor→监听者 / pool→sub_pool），不是单进程。
    按单 PID 数/杀会把树杀散导致连环崩（前几次翻车根因）。按树判定才安全。
    """
    if not pid:
        return set()
    anc = _ancestors(pid)
    # 逐级上溯到最顶层祖先作为树根
    root, cur = pid, pid
    while True:
        try:
            par = psutil.Process(cur).parent()
        except Exception:
            par = None
        if par is None:
            break
        root = cur = par.pid
    return _descendants([root]) | anc | {pid, root}


def enforce_singletons():
    """硬单实例（按进程树，非按单 PID）：唯一实例 = 端口监听者的整棵进程树；树外同类进程一律杀。

    - xinference-local / xoscar 池：以「9997 监听者的整棵树」为唯一活实例，树外的（另一个实例的
      supervisor、历史遗留的孤儿池）全杀——树内的一律不碰（保活实例的 supervisor 与它的池）。
    - 9997 无监听（服务确定已死）→ 所有 xoscar 池都是孤儿，全杀。
    - console：以「9998 健康监听者的整棵树」为准，树外全杀。
    - monitor：以「自己这棵树」为准，树外的重复监控全杀。
    - 🔴 自卫：绝不杀自己这棵树内的任何进程。
    返回实例计数字典。
    """
    counts = {"monitor": 1, "xinference": 0, "console": 0, "xoscar": 0}
    self_tree = _instance_tree(os.getpid())  # 自己这棵树，永不杀

    def _kill_outside(pid, tree, why):
        # 连根拔起：pid 在受护树(自己这棵 / 活实例那棵)之外 → 杀它整棵子树(父子全杀)，不留残枝。
        if pid in self_tree:
            return False
        if tree and pid in tree:
            return False
        victims = [pid] | sorted(_descendants([pid]))
        did = False
        for v in victims:
            if v in self_tree or (tree and v in tree):
                continue  # 受护树内的绝不误伤
            if _kill(v, why):
                did = True
        return did

    # ── monitor：自己这棵树外的都是重复监控 ──
    other_mons = [p[0] for p in count_procs("monitor_indextts.py")]
    for pid in other_mons:
        _kill_outside(pid, self_tree, "重复监控进程")
    counts["monitor"] = 1 + len([p for p in other_mons if p not in self_tree])

    # ── 唯一活 xinference 实例 = 9997 监听者的整棵树 ──
    listeners = listener_pids(PORT)
    live_tree = _instance_tree(listeners[0]) if listeners else set()

    # ── xinference-local：活实例树外全杀；无监听则不杀（可能启动中，交 ensure_service）──
    xinf = count_procs("xinference-local")
    counts["xinference"] = _count_instances(xinf)   # 按树根数实例，不是进程数(supervisor+worker=1)
    if live_tree:
        for pid, mem, _ in xinf:
            _kill_outside(pid, live_tree, "重复/游离 xinference-local(活实例树外)")

    # ── xoscar 池：活实例树外全杀；9997 无监听则全是孤儿全杀 ──
    xoscar = count_procs("xoscar")
    counts["xoscar"] = _count_instances(xoscar)     # 按树根数实例，不是进程数
    if xoscar:
        if live_tree:
            for pid, mem, _ in xoscar:
                _kill_outside(pid, live_tree, "孤儿/重复 xoscar 池(活实例树外)")
        else:
            for pid, mem, _ in xoscar:
                _kill_outside(pid, set(), "孤儿 xoscar 池(9997 无监听/服务已死)")

    # ── console：9998 健康监听者的树外全杀 ──
    console_procs = count_procs(os.path.join("console", "server.py"))
    counts["console"] = _count_instances(console_procs)   # 按树根数实例，不是进程数
    c_listeners = listener_pids(CONSOLE_PORT)
    c_tree = _instance_tree(c_listeners[0]) if (c_listeners and console_ready()) else set()
    if c_tree:
        for pid, mem, _ in console_procs:
            _kill_outside(pid, c_tree, "重复/僵死控制台(活 console 树外)")

    if any(counts[k] > 1 for k in counts):
        log("实例计数 monitor=%s xinference=%s console=%s xoscar=%s（按进程树硬去重）" %
            (counts["monitor"], counts["xinference"], counts["console"], counts["xoscar"]))
    return counts


# ---------------- 独立状态感知（#68：感知层独立于控制台，控制台死也能看）----------------
LAST_STATE = {}


def get_status_json():
    try:
        import urllib.request
        req = urllib.request.Request(ENDPOINT + "/status", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def get_model_names():
    try:
        import urllib.request
        req = urllib.request.Request(ENDPOINT + "/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("model_name") or m.get("id") for m in data.get("data", [])]
    except Exception:
        return None


def probe_state(instances=None):
    """聚合三路存活 + 实例计数（本函数完全不依赖控制台 9998，控制台死也如实标红）。"""
    xinf = service_ready()
    names = get_model_names()
    model = bool(names) and any(n in (MODEL, "IndexTTS2") for n in (names or []))
    console = console_ready()
    gpu = ""
    st = get_status_json()
    if st:
        workers = st.get("workers") or {}
        if workers:
            first = next(iter(workers.values()))
            g = (first.get("status") or {}).get("gpu-0") or {}
            if g:
                gpu = "%.1f/%.1f GB" % (g.get("mem_used", 0) / 1073741824,
                                         g.get("mem_total", 0) / 1073741824)
    return {"xinf": xinf, "model": model, "console": console, "gpu": gpu,
            "instances": instances or {"monitor": 1, "xinference": 0, "console": 0},
            "ts": int(time.time()), "endpoint": ENDPOINT, "model_name": MODEL}


def write_status_file(state):
    """把聚合状态落到项目根 STATUS.txt（K: 真实盘，用户文件管理器必见）。"""
    path = os.path.join(BASE_DIR, "STATUS.txt")
    sym = lambda b: "🟢" if b else "🔴"
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["ts"]))
    inst = state.get("instances") or {}
    dup = any(int(inst.get(k, d) or 0) > 1 for k, d in
              [("monitor", 1), ("xinference", 0), ("console", 0), ("xoscar", 0)])
    inst_line = "实例数(硬单实例):     monitor=%s xinference=%s console=%s xoscar=%s %s" % (
        inst.get("monitor", 1), inst.get("xinference", 0), inst.get("console", 0),
        inst.get("xoscar", 0),
        "⚠️ 多实例！已当场去重" if dup else "（均唯一，正常）")
    lines = [
        "Xinference 本地语音服务 · 存活状态（独立监控写入，不依赖控制台）",
        "更新时间: %s" % t,
        "──────────────────────────────────",
        "Xinference 服务 (9997): %s %s" % (sym(state["xinf"]), "运行中" if state["xinf"] else "已停止"),
        "IndexTTS-2.5 模型:     %s %s" % (sym(state["model"]), "已加载" if state["model"] else "未加载/异常"),
        "Web 控制台 (9998):     %s %s" % (sym(state["console"]), "运行中" if state["console"] else "已停止（状态页/本文件仍由独立监控提供）"),
        "GPU 占用:              %s" % (state["gpu"] or "—"),
        inst_line,
        "",
        "状态页（独立于控制台，控制台死也能看）: http://127.0.0.1:9996/status",
        "判读：控制台红灯但状态页可访问 => 控制台已死，监控仍在看守；状态页也打不开 => 监控已死（看上方更新时间）",
    ]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


# 独立微型状态 HTTP 服务（端口 9996，标准库零依赖，独立于 9998 控制台）
HEALTH_PORT = int(os.environ.get("MONITOR_STATUS_PORT", "9996"))
STATUS_HTML_PATH = os.path.join(BASE_DIR, "console", "status.html")


class _StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/health"):
            try:
                body = json.dumps(LAST_STATE, ensure_ascii=False).encode("utf-8")
            except Exception:
                body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/status") or self.path == "/":
            try:
                html = open(STATUS_HTML_PATH, "rb").read()
            except Exception:
                html = b"<h1>status.html missing</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def start_status_server():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", HEALTH_PORT), _StatusHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log("独立状态页服务已启动: http://127.0.0.1:%d/status（独立于 9998 控制台）" % HEALTH_PORT)
    except Exception as e:
        log("状态页服务启动失败（端口 %d 可能被占）: %s" % (HEALTH_PORT, e))


def _one_cycle():
    counts = enforce_singletons()      # 禁多实例：先数实例、杀多余/僵死，再谈自愈
    check_service_singleton()          # 9997 端口多监听者兜底
    ensure_service_and_model()         # 服务/模型自愈（内部已禁重复拉起）
    ensure_console()                   # 控制台自愈（内部已禁重复拉起）
    LAST_STATE.clear()
    LAST_STATE.update(probe_state(counts))
    write_status_file(LAST_STATE)


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

    # 独立状态页（9996）最先起：确保状态页秒开，绝不被后续扫除/自愈阻塞
    # （此前把"大扫除"放在起状态服务之前，一旦扫除卡住/误杀连带，9996 就起不来）
    start_status_server()

    if args.once:
        _one_cycle()
    else:
        while True:
            try:
                _one_cycle()
            except Exception:
                log("监控循环异常: %s" % traceback.format_exc())
            time.sleep(args.interval)
    release_lock(lock)


if __name__ == "__main__":
    main()
