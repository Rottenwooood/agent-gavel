"""浏览器进程管理（docs/tech-plan.md §5.2，docs/detection.md）。

三种拿到浏览器的方式（见 docs/detection.md）：
- **managed（默认）**：我们自己用加固参数启动 Chrome（持久真实 profile），再让
  Playwright `connect_over_cdp` 连上去。既能有真实 profile，又能开多个隔离 context，
  且不带 Playwright 的自动化启动参数 → 更不易被识别。
- **attach**：`AGENT_GAVEL_CDP_ENDPOINT` 指向一个已在跑的 Chrome（用户自己起的），
  我们只连接、不负责关闭。
- **playwright**：`AGENT_GAVEL_BROWSER_MODE=playwright`，回到 Playwright 直接 launch
  （带自动化痕迹，仅在需要 proxy 等 Playwright 特有参数时用）。
"""

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

from .errors import GavelError

_HEADLESS_TRUE = ("1", "true", "yes", "on")


def env_headless() -> bool:
    return os.environ.get("AGENT_GAVEL_HEADLESS", "").strip().lower() in _HEADLESS_TRUE


def _default_channel():
    """Playwright 模式的 channel：系统 Chrome 优先，缺则自带 chromium。"""
    if "AGENT_GAVEL_CHROME_CHANNEL" in os.environ:
        return os.environ.get("AGENT_GAVEL_CHROME_CHANNEL").strip() or None
    names = (("google-chrome", "google-chrome-stable") if not sys.platform.startswith("win")
             else ("chrome.exe",))
    if any(shutil.which(n) for n in names):
        return "chrome"
    if any(shutil.which(n) for n in ("chromium", "chromium-browser", "chrome")):
        return "chromium"
    return None


def _find_chrome_binary():
    """managed 模式：找到可用的 Chrome/Chromium 可执行文件。"""
    p = os.environ.get("AGENT_GAVEL_CHROME_BIN", "")
    if p and os.path.isfile(p):
        return p
    if sys.platform.startswith("win"):
        cands = []
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands.append(os.path.join(base, "Google", "Chrome",
                                          "Application", "chrome.exe"))
        cands += [shutil.which(n) for n in ("chrome.exe", "msedge.exe")]
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        q = shutil.which(n)
        if q:
            return q
    return None


def _browser_mode():
    return (os.environ.get("AGENT_GAVEL_BROWSER_MODE") or "managed").strip().lower()


def _default_profile_dir():
    from .profiles import active_profile_dir, default_profile_dir
    return os.environ.get("AGENT_GAVEL_BROWSER_PROFILE") \
        or active_profile_dir() or default_profile_dir()


def _launch_args():
    """启动参数：默认尽量少留自动化痕迹。

    - --disable-blink-features=AutomationControlled：关掉 navigator.webdriver。
    - 不再默认 --disable-gpu（会把 WebGL 压成 SwiftShader）。
      需要软件渲染用 AGENT_GAVEL_DISABLE_GPU=1。
    - AGENT_GAVEL_EXTRA_ARGS：追加自定义参数（空格分隔）。
    """
    args = ["--no-first-run", "--disable-blink-features=AutomationControlled"]
    if sys.platform.startswith("linux"):
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    if os.environ.get("AGENT_GAVEL_DISABLE_GPU", "").strip().lower() in _HEADLESS_TRUE:
        args.append("--disable-gpu")
    extra = os.environ.get("AGENT_GAVEL_EXTRA_ARGS", "").strip()
    if extra:
        args += extra.split()
    return args


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _kill_tree(pid, grace_s=3.0):
    if not pid:
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
            for _ in range(int(grace_s * 10)):
                if not _pid_alive(pid):
                    return
                time.sleep(0.1)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        else:
            os.killpg(pid, signal.SIGTERM)  # 优雅退出，让 Chrome 落盘
            for _ in range(int(grace_s * 10)):
                if not _pid_alive(pid):
                    return
                time.sleep(0.1)
            os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proc_cmdline(pid):
    """读进程命令行并按参数切分（兼容某些环境 /proc 不是 NUL 分隔的情况）。"""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except Exception:
        return None
    parts = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    if len(parts) <= 1:
        parts = raw.decode("utf-8", "replace").split()
    return parts


class _Reused:
    def __init__(self, pid):
        self.pid = pid


def _cdp_ready(port):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            json.loads(r.read())
            return True
    except Exception:
        return False


def _managed_pids(udd):
    """找出用同一 user_data_dir 且带我们自动化标志的 Chrome（=我们自己遗留的）。"""
    pids = []
    pdir = "/proc"
    if not os.path.isdir(pdir):
        return pids
    for pid in os.listdir(pdir):
        if not pid.isdigit():
            continue
        parts = _proc_cmdline(pid)
        if not parts or "chrome" not in os.path.basename(parts[0]).lower():
            continue
        if (f"--user-data-dir={udd}" in parts
                and "--disable-blink-features=AutomationControlled" in parts
                and "--remote-allow-origins=*" in parts):
            pids.append(int(pid))
    return pids


def _singleton_paths(udd):
    return [os.path.join(udd, n) for n in
            ("SingletonLock", "SingletonCookie", "SingletonSocket")]


def _clear_singletons(udd):
    for p in _singleton_paths(udd):
        try:
            os.remove(p)
        except OSError:
            pass


def _state_path(udd):
    return os.path.join(udd, ".agent-gavel-browser.json")


def _read_state(udd):
    try:
        with open(_state_path(udd), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_state(udd, pid, port):
    try:
        with open(_state_path(udd), "w", encoding="utf-8") as f:
            json.dump({"pid": int(pid), "port": int(port), "at": time.time()}, f)
    except Exception:
        pass


def _clear_state(udd):
    try:
        os.remove(_state_path(udd))
    except OSError:
        pass


def _reuse_existing(udd):
    """复用：优先读状态文件（MCP 重启后仍能重连），再退回 /proc 扫描。"""
    st = _read_state(udd)
    if st and st.get("pid") and st.get("port") \
            and _pid_alive(int(st["pid"])) and _cdp_ready(int(st["port"])):
        return int(st["pid"]), int(st["port"])
    pdir = "/proc"
    if not os.path.isdir(pdir):
        return None
    for pid in os.listdir(pdir):
        if not pid.isdigit():
            continue
        parts = _proc_cmdline(pid)
        if not parts or "chrome" not in os.path.basename(parts[0]).lower():
            continue
        if f"--user-data-dir={udd}" not in parts:
            continue
        port = None
        for a in parts:
            if a.startswith("--remote-debugging-port="):
                port = a.split("=", 1)[1]
                break
        if port and str(port).isdigit() and _cdp_ready(int(port)):
            return int(pid), int(port)
    return None


def _reconcile(udd):
    """启动前对账：能复用就复用；否则清掉我们遗留的 Chrome 并删除单例锁。

    返回 (pid, port) 表示可复用；None 表示需要干净启动。
    """
    ex = _reuse_existing(udd)
    if ex:
        return ex
    pids = _managed_pids(udd)
    if pids:
        for pid in pids:
            _kill_tree(pid, grace_s=3.0)
        _clear_singletons(udd)
        time.sleep(0.3)
    return None


class BrowserHandle:
    def __init__(self, process_id, *, browser=None, persistent_context=None,
                 mode="shared", opts=None, owned_pid=None):
        self.process_id = process_id
        self.browser = browser
        self.persistent_context = persistent_context
        self.mode = mode
        self.opts = opts or {}
        self.owned_pid = owned_pid
        self._closed = False

    @property
    def pid(self):
        return self.owned_pid

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        if self.browser is not None:
            try:
                return self.browser.is_connected()
            except Exception:
                return False
        return True

    def to_dict(self):
        return {"process_id": self.process_id, "mode": self.mode,
                "pid": self.pid, "alive": self.alive,
                "headless": self.opts.get("headless"),
                "channel": self.opts.get("channel"),
                "user_data_dir": self.opts.get("user_data_dir"),
                "proxy": bool(self.opts.get("proxy"))}


class BrowserProcessManager:
    def __init__(self):
        self._pw = None
        self._handles = {}
        self._lock = asyncio.Lock()
        self._n = 0

    async def _ensure_pw(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    @property
    def devices(self):
        return (self._pw.devices if self._pw is not None else {})

    def _next_id(self):
        self._n += 1
        return f"proc_{self._n:03d}"

    def _resolve_headless(self, override):
        return env_headless() if override is None else bool(override)

    async def _headless_default_ua(self, browser):
        """无头模式取一次真实 UA，去掉 'HeadlessChrome' 作为默认 context UA。"""
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            ua = await page.evaluate("navigator.userAgent")
            await ctx.close()
            return ua.replace("HeadlessChrome", "Chrome")
        except Exception:
            return None

    # ---- managed：自起加固 Chrome + 连接 ----
    async def _spawn_managed(self, udd, port, headless, extra_args=None):
        binary = _find_chrome_binary()
        if not binary:
            raise GavelError("chrome_env", "找不到 Chrome/Chromium 可执行文件",
                             hint="安装 Chrome，或设 AGENT_GAVEL_CHROME_BIN")
        os.makedirs(udd, exist_ok=True)
        # 启动前对账：能复用就复用（状态文件优先，重启后也能重连）；否则清掉遗留 + 删单例锁
        ex = _reconcile(udd)
        if ex:
            _write_state(udd, ex[0], ex[1])
            return _Reused(ex[0]), ex[1]
        args = [binary, *_launch_args()]
        if extra_args:
            args += list(extra_args)
        args += [f"--user-data-dir={udd}", f"--remote-debugging-port={port}",
                 "--remote-allow-origins=*", "--no-default-browser-check"]
        if headless:
            args += ["--headless=new", "--window-size=1920,1080"]
        args.append("about:blank")
        logf = open(os.path.join(udd, "chrome.log"), "ab")
        kwargs = {"stdout": logf, "stderr": logf}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(args, **kwargs)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                    json.loads(r.read())
                    _write_state(udd, proc.pid, port)
                    return proc, port
            except Exception:
                await asyncio.sleep(0.2)
        _kill_tree(proc.pid)
        _clear_singletons(udd)
        raise GavelError(
            "chrome_env", "自起 Chrome 的调试端口未就绪",
            detail={"user_data_dir": udd},
            hint="资料目录可能被其它 Chrome 占用；本项目已尝试清理自己的残留进程与单例锁，"
                 "仍失败请确认没有手动/外部 Chrome 在用同一 user_data_dir")

    async def _connect(self, pw, port_or_url):
        url = port_or_url if str(port_or_url).startswith("http") \
            else f"http://127.0.0.1:{port_or_url}"
        return await pw.chromium.connect_over_cdp(url)

    # ---- playwright：Playwright 直接 launch ----
    def _launch_kwargs(self, opts):
        headless = self._resolve_headless(opts.get("headless"))
        kw = {"headless": headless, "args": opts.get("args") or _launch_args()}
        if opts.get("slow_mo") is not None:
            kw["slow_mo"] = opts["slow_mo"]
        if opts.get("downloads_path"):
            kw["downloads_path"] = opts["downloads_path"]
        channel = opts.get("channel") or _default_channel()
        if channel:
            kw["channel"] = channel
        if opts.get("proxy"):
            kw["proxy"] = opts["proxy"]
        return kw, headless

    async def _launch_with_fallback(self, pw, kw, persistent_dir=None):
        alt = dict(kw)
        try:
            if persistent_dir is not None:
                return await pw.chromium.launch_persistent_context(
                    persistent_dir, accept_downloads=True, **alt)
            return await pw.chromium.launch(**alt)
        except Exception as e1:  # noqa: BLE001
            if "channel" not in alt:
                raise GavelError("chrome_env", f"浏览器启动失败：{e1}") from e1
            alt.pop("channel", None)
            try:
                if persistent_dir is not None:
                    return await pw.chromium.launch_persistent_context(
                        persistent_dir, accept_downloads=True, **alt)
                return await pw.chromium.launch(**alt)
            except Exception as e2:  # noqa: BLE001
                raise GavelError(
                    "chrome_env", f"浏览器启动失败（系统 Chrome 与自带 chromium 均失败）：{e2}",
                    detail={"chrome_error": str(e1)},
                    hint="装 Chrome，或执行 `playwright install chromium`") from e2

    # ---- 对外 ----
    async def launch(self, **opts):
        async with self._lock:
            pw = await self._ensure_pw()
            opts = dict(opts)
            endpoint = opts.pop("cdp_endpoint", None) \
                or os.environ.get("AGENT_GAVEL_CDP_ENDPOINT")
            mode = (opts.pop("browser_mode", None) or _browser_mode()).strip().lower()
            # 接管已存在的 Chrome（#2）：只连接，不负责关闭
            if endpoint:
                browser = await self._connect(pw, endpoint)
                h = BrowserHandle(self._next_id(), browser=browser,
                                  mode="attach", opts=opts)
                self._handles[h.process_id] = h
                return h
            # Playwright 直接 launch（带 proxy 等 Playwright 特有参数，或显式要求）
            if mode == "playwright" or opts.get("proxy") or opts.get("channel") \
                    or not _find_chrome_binary():
                kw, headless = self._launch_kwargs(opts)
                browser = await self._launch_with_fallback(pw, kw)
                opts["headless"] = headless
                if headless:
                    ua = await self._headless_default_ua(browser)
                    if ua:
                        opts["default_user_agent"] = ua
                h = BrowserHandle(self._next_id(), browser=browser,
                                  mode="shared", opts=opts)
                self._handles[h.process_id] = h
                return h
            # managed：自起加固 Chrome（持久真实 profile）+ 连接
            headless = self._resolve_headless(opts.get("headless"))
            udd = opts.get("user_data_dir") or _default_profile_dir()
            proc, port = await self._spawn_managed(udd, _free_port(), headless)
            browser = await self._connect(pw, port)
            opts["headless"] = headless
            opts["user_data_dir"] = udd
            if headless:
                ua = await self._headless_default_ua(browser)
                if ua:
                    opts["default_user_agent"] = ua
            h = BrowserHandle(self._next_id(), browser=browser,
                              mode="managed", opts=opts, owned_pid=proc.pid)
            self._handles[h.process_id] = h
            return h

    async def launch_persistent(self, user_data_dir, **opts):
        async with self._lock:
            pw = await self._ensure_pw()
            opts = dict(opts)
            endpoint = opts.pop("cdp_endpoint", None) \
                or os.environ.get("AGENT_GAVEL_CDP_ENDPOINT")
            mode = (opts.pop("browser_mode", None) or _browser_mode()).strip().lower()
            if endpoint:
                browser = await self._connect(pw, endpoint)
                ctx = browser.contexts[0] if browser.contexts \
                    else await browser.new_context()
                h = BrowserHandle(self._next_id(), browser=browser,
                                  persistent_context=ctx, mode="attach",
                                  opts=opts)
                self._handles[h.process_id] = h
                return h
            if mode == "playwright" or opts.get("proxy") \
                    or not _find_chrome_binary():
                kw, headless = self._launch_kwargs(opts)
                os.makedirs(user_data_dir, exist_ok=True)
                ctx = await self._launch_with_fallback(pw, kw,
                                                       persistent_dir=user_data_dir)
                opts["headless"] = headless
                opts["user_data_dir"] = user_data_dir
                h = BrowserHandle(self._next_id(), browser=ctx.browser,
                                  persistent_context=ctx, mode="persistent",
                                  opts=opts)
                self._handles[h.process_id] = h
                return h
            # managed 持久：自起 + 连接，用它的默认 context
            headless = self._resolve_headless(opts.get("headless"))
            proc, port = await self._spawn_managed(user_data_dir, _free_port(),
                                                   headless)
            browser = await self._connect(pw, port)
            ctx = browser.contexts[0] if browser.contexts \
                else await browser.new_context()
            opts["headless"] = headless
            opts["user_data_dir"] = user_data_dir
            h = BrowserHandle(self._next_id(), browser=browser,
                              persistent_context=ctx, mode="managed",
                              opts=opts, owned_pid=proc.pid)
            self._handles[h.process_id] = h
            return h

    def get(self, process_id):
        return self._handles.get(process_id)

    def list(self):
        return [h.to_dict() for h in self._handles.values()]

    def os_browsers(self):
        """OS 里真实存在、用我们资料目录的 Chrome（含 MCP 重启后丢失登记的孤儿）。"""
        udds = set()
        for h in self._handles.values():
            u = h.opts.get("user_data_dir")
            if u:
                udds.add(u)
        try:
            from .profiles import active_profile_dir, default_profile_dir
            for u in (active_profile_dir(), default_profile_dir()):
                if u:
                    udds.add(u)
        except Exception:
            pass
        registered = {h.owned_pid for h in self._handles.values() if h.owned_pid}
        out = []
        seen = set()
        for u in udds:
            for pid in _managed_pids(u):
                out.append({"pid": pid, "user_data_dir": u,
                            "registered": pid in registered})
                seen.add(pid)
            st = _read_state(u)
            if st and st.get("pid"):
                pid = int(st["pid"])
                if pid not in seen and _pid_alive(pid):
                    out.append({"pid": pid, "user_data_dir": u,
                                "registered": pid in registered})
        return out

    async def close(self, handle):
        if handle is None:
            return
        handle._closed = True
        self._handles.pop(handle.process_id, None)
        # 自起的 Chrome：先让它自己干净退出（CDP Browser.close），这样 localStorage
        # 等才会落盘；否则立刻 SIGTERM 会丢数据。退出后再兜底 kill。
        if handle.owned_pid:
            if handle.browser is not None:
                try:
                    cdp = await handle.browser.new_browser_cdp_session()
                    await cdp.send("Browser.close")
                except Exception:
                    pass
            for _ in range(80):
                if not _pid_alive(handle.owned_pid):
                    break
                await asyncio.sleep(0.1)
            try:
                if handle.browser is not None:
                    await handle.browser.close()
            except Exception:
                pass
            _kill_tree(handle.owned_pid, grace_s=5.0)
            udd = handle.opts.get("user_data_dir")
            if udd:
                _clear_state(udd)
                _clear_singletons(udd)
            return
        try:
            if handle.persistent_context is not None:
                await handle.persistent_context.close()
            elif handle.browser is not None:
                await handle.browser.close()
        except Exception:
            pass

    async def stop_all(self):
        for handle in list(self._handles.values()):
            await self.close(handle)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
