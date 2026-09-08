"""Chrome 进程自管理：agent-gavel 自己拉起/守护调试用 Chrome。

背景：DOM 通道此前依赖手动开 Chrome（--remote-debugging-port），
T1 后按需自启。要点：
  - 独立 user-data-dir（/tmp/agent-gavel-chrome），绝不碰日常浏览器
  - 只清理自己拉起的进程（pidfile 记录进程组），绝不误杀用户手动开的 Chrome
  - 每次 DomClient 连接前 ensure()：探测→无则拉起→轮询就绪
  - 崩溃自愈：下一次连接时探测发现死了，自动重拉
  - 退出清理：stop_own() 由 main.py 的 SIGTERM handler（父进程死亡）调用

对外 API：
  ensure_chrome(port) -> {"ok": bool, "owner": "self"|"external"|"started",
                           "cdp": bool, "error": str}
  stop_own()          -> 杀掉自己拉起的 Chrome 进程组（如有）
  status(port)        -> 探测当前状态（doctor 用，只读）
"""

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

# ---- 配置（环境变量可覆盖）----

DEBUG_PORT = int(os.environ.get("AGENT_GAVEL_CDP_PORT", "9222"))
USER_DATA_DIR = os.environ.get(
    "AGENT_GAVEL_CHROME_USER_DATA", "/tmp/agent-gavel-chrome")
PIDFILE = os.environ.get(
    "AGENT_GAVEL_CHROME_PIDFILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chrome.pid"))
LOGFILE = os.environ.get(
    "AGENT_GAVEL_CHROME_LOGFILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chrome.log"))
CHROME_BIN = os.environ.get("AGENT_GAVEL_CHROME_BIN", "")

# 启动 Chrome 用参数：独立 profile、开调试口、不弹"恢复会话"等干扰。
# 注意: --no-sandbox 在部分发行版必需；--disable-gpu 避免无头渲染告警。
_CHROME_FLAGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--metrics-recording-only",
]

_lock = threading.Lock()


def _log_dir():
    d = os.path.dirname(PIDFILE)
    os.makedirs(d, exist_ok=True)
    return d


def chrome_binary():
    """返回可用 Chrome 可执行文件路径。"""
    if CHROME_BIN and os.path.isfile(CHROME_BIN):
        return CHROME_BIN
    for name in ("google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _probe(port=DEBUG_PORT):
    """探测 CDP 调试口是否响应。只读，不改状态。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            v = json.loads(r.read())
            return {"ok": True, "browser": v.get("Browser", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _read_pidfile():
    try:
        with open(PIDFILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _write_pidfile(pid):
    _log_dir()
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(pid))


def _own_process_alive(pid):
    """pid 组是否存活且确实是"我们拉起的 Chrome"。

    只查 os.killpg 有 pid 复用竞态：进程组刚消失，pid 可能立刻被
    其它进程组占用导致误判。必须加 cmdline 校验——组 leader 的
    命令行要含我们的 user-data-dir 才算数。
    """
    if not pid:
        return False
    try:
        os.killpg(pid, 0)  # 组存在才继续
    except (ProcessLookupError, PermissionError):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        return USER_DATA_DIR in cmd
    except Exception:
        return False


def _headless_env():
    """Chrome 需要 X/Wayland 才能起图形窗口。若当前进程环境没有可用
    DISPLAY，自动加 --headless=new 兜底——agent-gavel 走 CDP 全自动化，
    不需要可见窗口；从 opencode/systemd/cron 等无显示环境拉起也能用。
    探测 DISPLAY 存在且能连才走有头，否则 headless。
    """
    disp = os.environ.get("DISPLAY", "")
    if not disp:
        return True
    try:
        r = subprocess.run(["xdpyinfo", "-display", disp],
                           capture_output=True, timeout=3)
        return r.returncode != 0
    except Exception:
        # xdpyinfo 不可用时不猜——返回 False(有头) 由 Chrome 自己报错
        return False


def _spawn(port=DEBUG_PORT):
    """拉起 Chrome（新进程组，组长 pid 记入 pidfile）。"""
    binary = chrome_binary()
    if not binary:
        return {"ok": False, "error": "no chrome binary found"}
    _log_dir()
    flags = list(_CHROME_FLAGS)
    if _headless_env():
        flags.append("--headless=new")
    cmd = [binary, *flags,
           f"--user-data-dir={USER_DATA_DIR}",
           f"--remote-debugging-port={port}",
           "about:blank"]
    try:
        with open(LOGFILE, "ab") as out:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=out, start_new_session=True)
    except Exception as e:
        return {"ok": False, "error": f"spawn failed: {e}"}
    _write_pidfile(proc.pid)
    return {"ok": True, "pid": proc.pid}


def ensure_chrome(port=DEBUG_PORT, timeout_s=20.0):
    """确保调试用 Chrome 在跑。探测→无则自启→轮询就绪。

    返回 {"ok", "owner", ...}：
      owner=external  端口已被(可能是用户手动开的)Chrome 占用——不动它
      owner=started   本次由 agent-gavel 拉起
      owner=self      之前 agent-gavel 拉起的还在跑
    """
    with _lock:
        probe = _probe(port)
        if probe.get("ok"):
            # 端口在响应。判断是不是自己起的：pidfile 有记录且进程组活着。
            pid = _read_pidfile()
            if pid and _own_process_alive(pid):
                return {"ok": True, "owner": "self",
                        "cdp": True, "browser": probe.get("browser")}
            return {"ok": True, "owner": "external",
                    "cdp": True, "browser": probe.get("browser")}

        # 探测失败：可能是没起，也可能是起了没就绪。先查自己起的是不是僵了。
        pid = _read_pidfile()
        if pid and _own_process_alive(pid):
            # 进程活着但 CDP 没就绪——等一会儿（首次启动要几秒）
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                p2 = _probe(port)
                if p2.get("ok"):
                    return {"ok": True, "owner": "self", "cdp": True,
                            "browser": p2.get("browser")}
                time.sleep(0.5)
            return {"ok": False, "owner": "self", "cdp": False,
                    "error": "our chrome alive but CDP not ready"}

        # 自己没起（或已死）：拉一个新的
        sp = _spawn(port)
        if not sp.get("ok"):
            return {"ok": False, "owner": "none", "cdp": False,
                    "error": sp.get("error")}
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            p2 = _probe(port)
            if p2.get("ok"):
                return {"ok": True, "owner": "started", "cdp": True,
                        "browser": p2.get("browser"), "pid": sp.get("pid")}
            time.sleep(0.5)
        return {"ok": False, "owner": "started", "cdp": False,
                "error": "spawned but CDP not ready",
                "pid": sp.get("pid")}


def stop_own():
    """杀掉 agent-gavel 自己拉起的 Chrome 进程组（如有）。

    只清理 pidfile 里有记录且进程组 leader 仍匹配的进程——
    端口若已被外部 Chrome 接管（我们已死），不误杀。
    """
    pid = _read_pidfile()
    if not pid or not _own_process_alive(pid):
        try:
            os.remove(PIDFILE)
        except Exception:
            pass
        return {"stopped": False, "reason": "no owned chrome"}
    try:
        os.killpg(pid, signal.SIGTERM)
        # 给 3s 优雅退出，再 SIGKILL 兜底
        for _ in range(30):
            if not _own_process_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.remove(PIDFILE)
    except Exception:
        pass
    return {"stopped": True, "pid": pid}


def status(port=DEBUG_PORT):
    """只读状态（doctor 用）：chrome 是否存在/谁起的/pidfile。"""
    probe = _probe(port)
    pid = _read_pidfile()
    mine = bool(pid and _own_process_alive(pid))
    return {
        "cdp_ready": probe.get("ok"),
        "browser": probe.get("browser", ""),
        "owner": "self" if mine else ("external" if probe.get("ok") else "none"),
        "pidfile_pid": pid,
        "port": port,
    }
