"""Playwright 浏览器进程管理（docs/tech-plan.md §5.2）。

新 runtime 独占的浏览器进程；不复用旧 browser_manager 的调试 Chrome。
- 普通模式：pw.chromium.launch(...) → 返回 Browser
- 持久模式：pw.chromium.launch_persistent_context(udd, ...) → 返回 BrowserContext
  （persistent context 的 browser 可能拿不到，句柄只保存 context）
- channel="chrome" 优先用系统 Chrome；失败回退 Playwright 自带 chromium
- headless 由参数 > AGENT_GAVEL_HEADLESS > 默认 headed
"""
import asyncio
import os
import shutil
import sys

from playwright.async_api import async_playwright

from .errors import GavelError

_HEADLESS_TRUE = ("1", "true", "yes", "on")


def env_headless() -> bool:
    return os.environ.get("AGENT_GAVEL_HEADLESS", "").strip().lower() in _HEADLESS_TRUE


def _default_channel():
    """按 docs/tech-plan.md §5.2：系统 Chrome 优先，缺则回退自带 chromium。

    AGENT_GAVEL_CHROME_CHANNEL 可显式覆盖；设为空字符串 = 强制用自带 chromium。
    """
    if "AGENT_GAVEL_CHROME_CHANNEL" in os.environ:
        return os.environ.get("AGENT_GAVEL_CHROME_CHANNEL").strip() or None
    names = (("google-chrome", "google-chrome-stable") if not sys.platform.startswith("win")
             else ("chrome.exe",))
    if any(shutil.which(n) for n in names):
        return "chrome"
    if any(shutil.which(n) for n in ("chromium", "chromium-browser", "chrome")):
        return "chromium"
    return None


def _launch_args():
    if sys.platform.startswith("linux"):
        return ["--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--no-first-run"]
    return ["--no-first-run"]


class BrowserHandle:
    def __init__(self, process_id, *, browser=None, persistent_context=None,
                 mode="shared", opts=None):
        self.process_id = process_id
        self.browser = browser
        self.persistent_context = persistent_context
        self.mode = mode
        self.opts = opts or {}
        self._closed = False

    @property
    def pid(self):
        return None  # Playwright 不公开浏览器 OS pid

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
        """尝试带 channel，失败去掉 channel 回退自带 chromium。"""
        alt = dict(kw)
        try:
            if persistent_dir is not None:
                return await pw.chromium.launch_persistent_context(
                    persistent_dir, accept_downloads=True, **alt)
            return await pw.chromium.launch(**alt)
        except Exception as e1:  # noqa: BLE001
            if "channel" not in alt:
                raise GavelError(
                    "chrome_env",
                    f"浏览器启动失败：{e1}",
                    hint="确认已装 Chrome，或在 MCP environment 里设 "
                         "AGENT_GAVEL_HEADLESS=1 走无头",
                ) from e1
            alt.pop("channel", None)
            try:
                if persistent_dir is not None:
                    return await pw.chromium.launch_persistent_context(
                        persistent_dir, accept_downloads=True, **alt)
                return await pw.chromium.launch(**alt)
            except Exception as e2:  # noqa: BLE001
                raise GavelError(
                    "chrome_env",
                    f"浏览器启动失败（系统 Chrome 与自带 chromium 均失败）：{e2}",
                    detail={"chrome_error": str(e1)},
                    hint="装 Chrome，或执行 `playwright install chromium`",
                ) from e2

    async def launch(self, **opts):
        async with self._lock:
            pw = await self._ensure_pw()
            kw, headless = self._launch_kwargs(opts)
            browser = await self._launch_with_fallback(pw, kw)
            opts = dict(opts)
            opts["headless"] = headless
            handle = BrowserHandle(self._next_id(), browser=browser,
                                   mode="shared", opts=opts)
            self._handles[handle.process_id] = handle
            return handle

    async def launch_persistent(self, user_data_dir, **opts):
        async with self._lock:
            pw = await self._ensure_pw()
            kw, headless = self._launch_kwargs(opts)
            os.makedirs(user_data_dir, exist_ok=True)
            ctx = await self._launch_with_fallback(pw, kw,
                                                   persistent_dir=user_data_dir)
            opts = dict(opts)
            opts["headless"] = headless
            opts["user_data_dir"] = user_data_dir
            handle = BrowserHandle(self._next_id(), browser=ctx.browser,
                                   persistent_context=ctx, mode="persistent",
                                   opts=opts)
            self._handles[handle.process_id] = handle
            return handle

    def get(self, process_id):
        return self._handles.get(process_id)

    def list(self):
        return [h.to_dict() for h in self._handles.values()]

    async def close(self, handle):
        if handle is None:
            return
        handle._closed = True
        self._handles.pop(handle.process_id, None)
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
