"""Chrome 进程自管理：agent-gavel 自己拉起/守护调试用 Chrome。

背景：DOM 通道此前依赖手动开 Chrome（--remote-debugging-port），
T1 后按需自启。要点：
  - 独立 user-data-dir（Linux /tmp/agent-gavel-chrome；Windows 系统临时目录），
    绝不碰日常浏览器
  - 只清理自己拉起的进程（pidfile 记录进程组），绝不误杀用户手动开的 Chrome
  - 每次 DomClient 连接前 ensure()：探测→无则拉起→轮询就绪
  - 崩溃自愈：下一次连接时探测发现死了，自动重拉
  - 退出清理：stop_own() 由 main.py 的退出 handler（父进程死亡）调用

可选无头：默认有头（可见窗口）。设 AGENT_GAVEL_HEADLESS=1 走 --headless=new，
无桌面会话也能跑（服务器/CI）；无头时跳过 DISPLAY 检查与窗口置顶。

Windows 适配说明（相对 Linux 版差异）：
  - Chrome 路径发现：额外探测 chrome.exe/msedge.exe 的常见安装路径
  - 无 DISPLAY 概念：可见窗口即桌面会话，直接 spawn
  - 进程存活/归属校验用 psutil（pid_exists + cmdline 含 user-data-dir），
    不再依赖 /proc 与 os.killpg
  - 前台置顶：Windows 用 user32（EnumWindows + ShowWindow +
    SetForegroundWindow，best-effort）；Linux 维持 xdotool
  - 清理：Windows 用 taskkill /T（进程树，先温和后强杀）；Linux 维持 killpg

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
import sys
import tempfile
import threading
import time
import urllib.request

import psutil

# ---- 配置（环境变量可覆盖）----

DEBUG_PORT = int(os.environ.get("AGENT_GAVEL_CDP_PORT", "9222"))

# 平台中立的数据根目录：Linux 沿用 /tmp/agent-gavel，Windows 用系统临时目录。
_DATA_ROOT = os.environ.get(
    "AGENT_GAVEL_DATA_DIR",
    os.path.join(tempfile.gettempdir(), "agent-gavel"),
)
USER_DATA_DIR = os.environ.get(
    "AGENT_GAVEL_CHROME_USER_DATA", os.path.join(_DATA_ROOT, "chrome"))
PIDFILE = os.environ.get(
    "AGENT_GAVEL_CHROME_PIDFILE", os.path.join(_DATA_ROOT, "chrome.pid"))
LOGFILE = os.environ.get(
    "AGENT_GAVEL_CHROME_LOGFILE", os.path.join(_DATA_ROOT, "chrome.log"))
CHROME_BIN = os.environ.get("AGENT_GAVEL_CHROME_BIN", "")

_IS_WINDOWS = os.name == "nt"

# 可选无头模式：默认有头（可见窗口，用户能看操作过程）。设
# AGENT_GAVEL_HEADLESS=1 切无头（服务器/CI/无桌面会话用）。进程级开关——
# 在 MCP 客户端的 environment 字段里设（见 README），不在 shell 里 export。
_HEADLESS = os.environ.get(
    "AGENT_GAVEL_HEADLESS", "").strip().lower() in ("1", "true", "yes", "on")

# 启动 Chrome 用参数：独立 profile、开调试口、不弹"恢复会话"等干扰。
# 注: --no-sandbox 在部分 Linux 发行版必需；--disable-gpu 避免无头渲染告警。
# Windows 上无需 --no-sandbox（保持默认沙箱），其它开关通用。
_CHROME_FLAGS = [
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--metrics-recording-only",
    # Chrome 111+ 拒绝非 DevTools 客户端的 CDP WebSocket(Origin 校验)；
    # 端口仅绑定 127.0.0.1，放开 Origin 校验无暴露面。
    "--remote-allow-origins=*",
]
if not _IS_WINDOWS:
    _CHROME_FLAGS.insert(0, "--no-sandbox")

_lock = threading.Lock()


def _log_dir():
    d = os.path.dirname(PIDFILE)
    os.makedirs(d, exist_ok=True)
    return d


def _chrome_candidates():
    """Windows: 常见安装路径 + PATH。返回按优先级排序的可执行文件路径。"""
    out = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    for name in ("chrome.exe", "msedge.exe", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            out.append(p)
    return out


def chrome_binary():
    """返回可用 Chrome 可执行文件路径。"""
    if CHROME_BIN and os.path.isfile(CHROME_BIN):
        return CHROME_BIN
    if _IS_WINDOWS:
        for p in _chrome_candidates():
            if p and os.path.isfile(p):
                return p
        return None
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


def _bring_to_front(pid=None):
    """把调试 Chrome 窗口置前可见（best-effort，失败不影响且要快）。

    用户要看着浏览器操作，所以每次 ensure 后就把它带到前台。
    - Windows: user32 EnumWindows 找属于我们 Chrome 主进程的最上层可见窗口，
      ShowWindow(SW_RESTORE) + SetForegroundWindow（前台锁定失败也忽略）。
    - Linux: xdotool search --class Google-chrome + windowactivate（非阻塞）。
      注意：windowactivate --sync 会阻塞等激活完成，GNOME/Wayland 常等满超时
      曾实测每次固定吃 5s——绝不用 --sync。
    """
    if _HEADLESS:
        return False  # 无头没有窗口可置顶
    if _IS_WINDOWS:
        return _win_bring_to_front(pid)
    return _x11_bring_to_front()


def _win_bring_to_front(pid):
    if not pid:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum_cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            wpid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return False  # 找到即停
            return True

        user32.EnumWindows(_enum_cb, 0)
        return True
    except Exception:
        return False


def _x11_bring_to_front():
    try:
        if not shutil.which("xdotool"):
            return False
        sr = subprocess.run(["xdotool", "search", "--onlyvisible",
                             "--class", "Google-chrome"],
                            capture_output=True, timeout=1)
        if sr.returncode != 0 or not sr.stdout.strip():
            return False
        wid = sr.stdout.decode().strip().split("\n")[0]
        subprocess.run(["xdotool", "windowactivate", wid],
                       capture_output=True, timeout=1)
        return True
    except Exception:
        return False


def _own_process_alive(pid):
    """pid 是否存活且确实是"我们拉起的 Chrome"。

    psutil 跨平台：进程不存在 → False；存在但命令行不含我们的
    user-data-dir（pid 已被复用/外部 Chrome）→ False。
    """
    if not pid:
        return False
    try:
        if not psutil.pid_exists(pid):
            return False
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline() or [])
        return USER_DATA_DIR in cmd
    except Exception:
        return False


def _spawn(port=DEBUG_PORT):
    """拉起 Chrome（组长 pid 记入 pidfile）。

    默认有头：调试 Chrome 用可见窗口（用户要看操作过程），Linux 无 DISPLAY
    时报错；Windows 桌面会话天然有窗口，跳过该检查。设 AGENT_GAVEL_HEADLESS=1
    则走无头（--headless=new），此时不要求 DISPLAY。
    """
    binary = chrome_binary()
    if not binary:
        return {"ok": False, "error": "no chrome binary found"}
    if not _IS_WINDOWS and not _HEADLESS:
        disp = os.environ.get("DISPLAY", "") or os.environ.get("WAYLAND_DISPLAY", "")
        if not disp:
            return {"ok": False, "error":
                    "DISPLAY 未设置——调试 Chrome 默认要可见窗口。请在桌面会话里"
                    "运行，或 export DISPLAY=:0；无桌面会话可切无头：在 MCP 客户端"
                    "的 environment 里设 AGENT_GAVEL_HEADLESS=1（见 README）"}
    _log_dir()
    flags = list(_CHROME_FLAGS)
    if _HEADLESS:
        # 新版无头（Chrome 112+），渲染/UA 更接近有头；固定窗口尺寸避免默认
        # 800x600 影响响应式布局。
        flags += ["--headless=new", "--window-size=1920,1080"]
    cmd = [binary, *flags,
           f"--user-data-dir={USER_DATA_DIR}",
           f"--remote-debugging-port={port}",
           "about:blank"]
    try:
        with open(LOGFILE, "ab") as out:
            kwargs = dict(stdout=out, stderr=out)
            if _IS_WINDOWS:
                # 新进程组：便于识别；进程树清理交给 psutil/taskkill
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **kwargs)
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
                _bring_to_front(pid)
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
                    _bring_to_front(pid)
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
                _bring_to_front(sp.get("pid"))
                return {"ok": True, "owner": "started", "cdp": True,
                        "browser": p2.get("browser"), "pid": sp.get("pid")}
            time.sleep(0.5)
        return {"ok": False, "owner": "started", "cdp": False,
                "error": "spawned but CDP not ready",
                "pid": sp.get("pid")}


def _stop_tree_windows(pid):
    """Windows：taskkill /T 温和退出进程树，未退净再 /F 强杀。"""
    def _run(extra):
        try:
            subprocess.run(["taskkill", *extra, "/PID", str(pid), "/T"],
                           capture_output=True, timeout=5)
        except Exception:
            pass
    _run([])
    for _ in range(30):
        if not _own_process_alive(pid):
            return
        time.sleep(0.1)
    _run(["/F"])


def _stop_tree_posix(pid):
    """Linux/macOS：killpg SIGTERM 优雅退出，超时 SIGKILL 兜底。"""
    try:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(30):
            if not _own_process_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass


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
    if _IS_WINDOWS:
        _stop_tree_windows(pid)
    else:
        _stop_tree_posix(pid)
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
        "mode": "headless" if _HEADLESS else "headed",
        "pidfile_pid": pid,
        "port": port,
    }
