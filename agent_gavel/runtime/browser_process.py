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
import re
import signal
import socket
import shutil
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

from ..core import config
from ..core.errors import GavelError


def _default_channel():
    return config.chrome_channel()


def _find_chrome_binary():
    """managed 模式：找到可用的 Chrome/Chromium 可执行文件。"""
    return config.chrome_bin()


def _guard_primary_headless(udd, headless):
    """主资料目录禁止无头：同一份登录资料被 headless 摸过一次，指纹就脏了。"""
    if not headless or not config.primary_headless_guard():
        return
    from .profiles import default_profile_dir

    if os.path.normcase(os.path.abspath(udd)) == os.path.normcase(
        os.path.abspath(default_profile_dir())
    ):
        raise GavelError(
            "headless_forbidden_on_primary",
            "默认资料目录禁止无头启动（会污染登录指纹）",
            hint="无头任务请显式传 profile 名或 container=isolated",
        )


def _default_profile_dir(profile=None, explicit=None):
    from .profiles import resolve_dir

    return resolve_dir(explicit=explicit, profile=profile)


def _launch_args():
    """启动参数：默认尽量少留自动化痕迹。

    - --disable-blink-features=AutomationControlled：关掉 navigator.webdriver。
    - 不再默认 --disable-gpu（会把 WebGL 压成 SwiftShader）。
      需要软件渲染用 AGENT_GAVEL_DISABLE_GPU=1。
    - AGENT_GAVEL_EXTRA_ARGS：追加自定义参数（空格分隔）。
    """
    # --keep-alive-for-test：关闭最后一个标签页时不退出浏览器进程。
    # 自动化场景下"关标签"不等于"关浏览器"，否则复用连接会拿到已退出的进程。
    args = [
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        "--keep-alive-for-test",
    ]
    if sys.platform.startswith("linux"):
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
        # 复制来的资料里 cookie 是钥匙串密钥加密的（v10/v11）；不显式指定
        # 密码存储，Chrome 会按默认探测失败并静默丢弃这批 cookie —— 登录态就没了。
        if shutil.which("secret-tool"):
            args.append("--password-store=gnome-libsecret")
    if config.disable_gpu():
        args.append("--disable-gpu")
    extra = config.extra_args()
    if extra:
        args += extra.split()
    return args


def _chrome_version(binary):
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return None
    found = re.search(r"(\d+\.\d+\.\d+\.\d+)", out or "")
    return found.group(1) if found else None


def _desktop_user_agent(version):
    """无头启动时给默认容器用的桌面 UA，避免 navigator.userAgent 暴露 HeadlessChrome。"""
    v = version or "140.0.0.0"
    if sys.platform.startswith("win"):
        platform = "Windows NT 10.0; Win64; x64"
    elif sys.platform == "darwin":
        platform = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        platform = "X11; Linux x86_64"
    return (
        "Mozilla/5.0 (" + platform + ") AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/" + v + " Safari/537.36"
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _kill_tree(pid, grace_s=3.0):
    if not pid:
        return
    if sys.platform.startswith("win"):
        try:
            await asyncio.to_thread(
                subprocess.run, ["taskkill", "/T", "/PID", str(pid)], capture_output=True, timeout=5
            )
            for _ in range(int(grace_s * 10)):
                if not _pid_alive(pid):
                    return
                await asyncio.sleep(0.1)
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        return
    try:
        os.killpg(pid, signal.SIGTERM)  # 优雅退出，让 Chrome 落盘
    except (ProcessLookupError, PermissionError, OSError):
        return
    for _ in range(int(grace_s * 10)):
        if not _pid_alive(pid):
            return
        await asyncio.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass  # silent-ok: best-effort，失败不影响主流程


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
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
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
        if (
            f"--user-data-dir={udd}" in parts
            and "--disable-blink-features=AutomationControlled" in parts
            and "--remote-allow-origins=*" in parts
        ):
            pids.append(int(pid))
    return pids


def _singleton_paths(udd):
    return [os.path.join(udd, n) for n in ("SingletonLock", "SingletonCookie", "SingletonSocket")]


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
        pass  # silent-ok: best-effort，失败不影响主流程


def _clear_state(udd):
    try:
        os.remove(_state_path(udd))
    except OSError:
        pass


def _reuse_existing(udd):
    """复用：优先读状态文件（MCP 重启后仍能重连），再退回 /proc 扫描。"""
    st = _read_state(udd)
    if (
        st
        and st.get("pid")
        and st.get("port")
        and _pid_alive(int(st["pid"]))
        and _cdp_ready(int(st["port"]))
    ):
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


async def _reconcile(udd):
    """启动前对账：能复用就复用；否则清掉我们遗留的 Chrome 并删除单例锁。

    返回 (pid, port) 表示可复用；None 表示需要干净启动。
    """
    ex = await asyncio.to_thread(_reuse_existing, udd)
    if ex:
        return ex
    pids = await asyncio.to_thread(_managed_pids, udd)
    if pids:
        for pid in pids:
            await _kill_tree(pid, grace_s=3.0)
        _clear_singletons(udd)
        await asyncio.sleep(0.3)
    return None


class BrowserHandle:
    def __init__(
        self,
        process_id,
        *,
        browser=None,
        persistent_context=None,
        mode="shared",
        opts=None,
        owned_pid=None,
    ):
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
        info = {
            "process_id": self.process_id,
            "mode": self.mode,
            "pid": self.pid,
            "alive": self.alive,
            "headless": self.opts.get("headless"),
            "channel": self.opts.get("channel"),
            "user_data_dir": self.opts.get("user_data_dir"),
            "proxy": bool(self.opts.get("proxy")),
        }
        if self.opts.get("mode_resolved"):
            info["mode_resolved"] = self.opts["mode_resolved"]
            info["mode_warning"] = self.opts.get("mode_warning")
        return info


class BrowserProcessManager:
    def __init__(self):
        self._pw = None
        self._handles = {}
        self._by_udd = {}
        self._lock = asyncio.Lock()
        self._n = 0

    async def _ensure_pw(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    @property
    def devices(self):
        return self._pw.devices if self._pw is not None else {}

    def _next_id(self):
        self._n += 1
        return f"proc_{self._n:03d}"

    def _resolve_headless(self, override):
        return config.headless() if override is None else bool(override)

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
            raise GavelError(
                "chrome_env",
                "找不到 Chrome/Chromium 可执行文件",
                hint="安装 Chrome，或设 AGENT_GAVEL_CHROME_BIN",
            )
        os.makedirs(udd, exist_ok=True)
        # 启动前对账：能复用就复用（状态文件优先，重启后也能重连）；否则清掉遗留 + 删单例锁
        ex = await _reconcile(udd)
        if ex:
            _write_state(udd, ex[0], ex[1])
            return _Reused(ex[0]), ex[1]
        args = [binary, *_launch_args()]
        if extra_args:
            args += list(extra_args)
        args += [
            f"--user-data-dir={udd}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-default-browser-check",
        ]
        if headless:
            args += ["--headless=new", "--window-size=1920,1080"]
            # 默认持久容器无法事后改 UA，只能在启动参数里给桌面 UA。
            version = await asyncio.to_thread(_chrome_version, binary)
            args.append(f"--user-agent={_desktop_user_agent(version)}")
        args.append("about:blank")
        logf = await asyncio.to_thread(open, os.path.join(udd, "chrome.log"), "ab")
        kwargs = {"stdout": logf, "stderr": logf}
        child_env = config.chrome_child_env()
        if child_env:
            kwargs["env"] = child_env
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = await asyncio.to_thread(subprocess.Popen, args, **kwargs)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if await asyncio.to_thread(_cdp_ready, port):
                _write_state(udd, proc.pid, port)
                return proc, port
            await asyncio.sleep(0.2)
        await _kill_tree(proc.pid)
        _clear_singletons(udd)
        raise GavelError(
            "chrome_env",
            "自起 Chrome 的调试端口未就绪",
            detail={"user_data_dir": udd},
            hint="资料目录可能被其它 Chrome 占用；本项目已尝试清理自己的残留进程与单例锁，"
            "仍失败请确认没有手动/外部 Chrome 在用同一 user_data_dir",
        )

    async def _connect(self, pw, port_or_url):
        url = (
            port_or_url
            if str(port_or_url).startswith("http")
            else f"http://127.0.0.1:{port_or_url}"
        )
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
                    persistent_dir, accept_downloads=True, **alt
                )
            return await pw.chromium.launch(**alt)
        except Exception as e1:  # noqa: BLE001
            if "channel" not in alt:
                raise GavelError("chrome_env", f"浏览器启动失败：{e1}") from e1
            alt.pop("channel", None)
            try:
                if persistent_dir is not None:
                    return await pw.chromium.launch_persistent_context(
                        persistent_dir, accept_downloads=True, **alt
                    )
                return await pw.chromium.launch(**alt)
            except Exception as e2:  # noqa: BLE001
                raise GavelError(
                    "chrome_env",
                    f"浏览器启动失败（系统 Chrome 与自带 chromium 均失败）：{e2}",
                    detail={"chrome_error": str(e1)},
                    hint="装 Chrome，或执行 `playwright install chromium`",
                ) from e2

    # ---- 对外 ----
    async def launch(self, **opts):
        return await self._start(None, **opts)

    def profile_dir(self, profile=None, explicit=None):
        """本次启动将使用的资料目录：显式 > 命名 profile > 默认(primary)。"""
        return _default_profile_dir(profile=profile, explicit=explicit)

    async def launch_persistent(self, user_data_dir, **opts):
        return await self._start(user_data_dir, **opts)

    async def _start(self, persistent_dir=None, **opts):
        """启动或接管的唯一实现；persistent_dir 非空时使用资料目录的默认 context。"""
        async with self._lock:
            pw = await self._ensure_pw()
            opts = dict(opts)
            endpoint = opts.pop("cdp_endpoint", None) or config.cdp_endpoint()
            requested = (opts.pop("browser_mode", None) or config.browser_mode()).strip().lower()
            notes = {}

            if endpoint:
                browser = await self._connect(pw, endpoint)
                ctx = None
                if persistent_dir:
                    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                h = BrowserHandle(
                    self._next_id(),
                    browser=browser,
                    persistent_context=ctx,
                    mode="attach",
                    opts=opts,
                )
                self._handles[h.process_id] = h
                return h

            reason = None
            if requested == "playwright":
                reason = "explicit"
            elif opts.get("proxy"):
                reason = "proxy"
            elif opts.get("channel"):
                reason = "channel"
            elif not _find_chrome_binary():
                reason = "no_chrome_binary"
            if requested == "managed" and reason:
                notes["mode_resolved"] = "playwright"
                notes["mode_warning"] = f"managed 模式不适用（{reason}），已改用 playwright"

            if reason:
                kw, headless = self._launch_kwargs(opts)
                if persistent_dir:
                    os.makedirs(persistent_dir, exist_ok=True)
                    ctx = await self._launch_with_fallback(pw, kw, persistent_dir=persistent_dir)
                    browser = ctx.browser
                    mode = "persistent"
                else:
                    ctx = None
                    browser = await self._launch_with_fallback(pw, kw)
                    mode = "shared"
                opts["headless"] = headless
                if persistent_dir:
                    opts["user_data_dir"] = persistent_dir
                if headless:
                    ua = await self._headless_default_ua(browser)
                    if ua:
                        opts["default_user_agent"] = ua
                h = BrowserHandle(
                    self._next_id(),
                    browser=browser,
                    persistent_context=ctx,
                    mode=mode,
                    opts={**opts, **notes},
                )
                self._handles[h.process_id] = h
                return h

            # managed：自起加固 Chrome（持久资料目录）+ Playwright 连接
            headless = self._resolve_headless(opts.get("headless"))
            udd = persistent_dir or opts.get("user_data_dir") or _default_profile_dir()
            _guard_primary_headless(udd, headless)
            proc, port = await self._spawn_managed(udd, _free_port(), headless)
            browser = await self._connect(pw, port)
            ctx = None
            if persistent_dir:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            opts["headless"] = headless
            opts["user_data_dir"] = udd
            if headless:
                ua = await self._headless_default_ua(browser)
                if ua:
                    opts["default_user_agent"] = ua
            h = BrowserHandle(
                self._next_id(),
                browser=browser,
                persistent_context=ctx,
                mode="managed",
                opts={**opts, **notes},
                owned_pid=proc.pid,
            )
            self._handles[h.process_id] = h
            return h

    def get(self, process_id):
        return self._handles.get(process_id)

    def matches(
        self,
        handle,
        *,
        headless=None,
        channel=None,
        proxy=None,
        browser_mode=None,
        cdp_endpoint=None,
    ):
        """已有浏览器与请求参数是否一致（None 表示不限定）。"""
        if handle is None or not handle.alive:
            return False
        opts = handle.opts or {}
        if headless is not None and bool(opts.get("headless")) != bool(headless):
            return False
        if proxy is not None and bool(opts.get("proxy")) != bool(proxy):
            return False
        if channel is not None and opts.get("channel") != channel:
            return False
        attach = (browser_mode or config.browser_mode()).strip().lower() == "attach" or bool(
            cdp_endpoint or config.cdp_endpoint()
        )
        return not (attach and handle.mode != "attach")

    def list(self):
        return [h.to_dict() for h in self._handles.values()]

    def kill_all_sync(self):
        """进程退出时的兜底回收：只杀我们自己启动的浏览器。"""
        for handle in list(self._handles.values()):
            if not handle.owned_pid:
                continue
            try:
                if sys.platform.startswith("win"):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(handle.owned_pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    os.killpg(handle.owned_pid, signal.SIGKILL)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    def os_browsers(self):
        """OS 里真实存在、用我们资料目录的 Chrome（含 MCP 重启后丢失登记的孤儿）。"""
        udds = set()
        for h in self._handles.values():
            u = h.opts.get("user_data_dir")
            if u:
                udds.add(u)
        try:
            from .profiles import default_profile_dir, list_profiles

            if default_profile_dir():
                udds.add(default_profile_dir())
            for item in list_profiles():
                udds.add(str(config.browser_profiles_root() / item["name"]))
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        registered = {h.owned_pid for h in self._handles.values() if h.owned_pid}
        out = []
        seen = set()
        for u in udds:
            for pid in _managed_pids(u):
                out.append({"pid": pid, "user_data_dir": u, "registered": pid in registered})
                seen.add(pid)
            st = _read_state(u)
            if st and st.get("pid"):
                pid = int(st["pid"])
                if pid not in seen and _pid_alive(pid):
                    out.append({"pid": pid, "user_data_dir": u, "registered": pid in registered})
        return out

    async def close(self, handle):
        if handle is None:
            return
        handle._closed = True
        self._handles.pop(handle.process_id, None)
        # 自起的 Chrome：先让它自己干净退出（CDP Browser.close），这样 localStorage
        # 等才会落盘；否则立刻 SIGTERM 会丢数据。退出后再兜底 kill。
        # 注意：浏览器可能在响应到达前就断开，await 必须带超时，否则会永久卡住。
        if handle.owned_pid:
            if handle.browser is not None:
                try:
                    cdp = await asyncio.wait_for(
                        handle.browser.new_browser_cdp_session(), timeout=3
                    )
                    await asyncio.wait_for(cdp.send("Browser.close"), timeout=3)
                except Exception:
                    pass  # silent-ok: best-effort，失败不影响主流程
            for _ in range(50):
                if not _pid_alive(handle.owned_pid):
                    break
                await asyncio.sleep(0.1)
            try:
                if handle.browser is not None:
                    await asyncio.wait_for(handle.browser.close(), timeout=3)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
            await _kill_tree(handle.owned_pid, grace_s=3.0)
            udd = handle.opts.get("user_data_dir")
            if udd:
                _clear_state(udd)
                _clear_singletons(udd)
            return
        try:
            if handle.persistent_context is not None:
                await asyncio.wait_for(handle.persistent_context.close(), timeout=5)
            elif handle.browser is not None:
                await asyncio.wait_for(handle.browser.close(), timeout=5)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程

    def _udd_key(self, udd):
        return os.path.normcase(os.path.abspath(udd))

    async def ensure(self, udd, **opts):
        """按资料目录取浏览器：内存里有活句柄就复用，否则启动/重连。"""
        key = self._udd_key(udd)
        hit = self._by_udd.get(key)
        if hit is not None and hit.alive:
            want = opts.get("headless")
            if want is not None and bool((hit.opts or {}).get("headless")) != bool(want):
                raise GavelError(
                    "headless_mismatch",
                    f"资料目录 {udd} 已有{'无头' if hit.opts.get('headless') else '有头'}浏览器在运行，"
                    f"不能再以{'无头' if want else '有头'}方式启动",
                    hint="先 browser(op=close, profile=...) 再换模式",
                )
            if self.matches(hit, **opts):
                return hit
        handle = await self.launch(user_data_dir=udd, **opts)
        self._by_udd[key] = handle
        return handle

    async def disconnect_all(self):
        """只断开连接、保留受管 Chrome 进程（空闲退出用）。"""
        for handle in list(self._handles.values()):
            handle._closed = True
        self._handles.clear()
        self._by_udd.clear()
        if self._pw is not None:
            try:
                await asyncio.wait_for(self._pw.stop(), timeout=5)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
            self._pw = None

    async def stop_all(self):
        for handle in list(self._handles.values()):
            await self.close(handle)
        if self._pw is not None:
            try:
                await asyncio.wait_for(self._pw.stop(), timeout=5)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
            self._pw = None
