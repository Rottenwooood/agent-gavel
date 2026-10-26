"""顶层运行时单例（docs/tech-plan.md §5.3）。

持有全部注册表与生命周期：processes / sessions / contexts / pages，
以及 events / metrics / policies / artifacts。tools 层只与本类交互。
"""

import asyncio
from collections import defaultdict

from .recorder import Recorder
from .artifacts import ArtifactStore
from .browser_process import BrowserProcessManager
from .contexts import ContextManager
from .events import EventBus
from .metrics import MetricsRecorder
from .pages import PageManager
from .policies import PolicyManager
from .sessions import Session, SessionManager
from .tracing import NetworkRecorder, Tracer


class BrowserRuntime:
    def __init__(self):
        self.processes = BrowserProcessManager()
        self.events = EventBus()
        self.metrics = MetricsRecorder()
        self.policies = PolicyManager()
        self.artifacts = ArtifactStore()
        self.tracer = Tracer()
        self.network = NetworkRecorder()
        self.recorder = Recorder()

        self.sessions = {}
        self.contexts = {}
        self.pages = {}
        self.runs = {}
        self._counters = defaultdict(int)

        self.session_mgr = SessionManager(self)
        self.context_mgr = ContextManager(self)
        self.page_mgr = PageManager(self)

        self._default_handle = None
        self._default_lock = asyncio.Lock()
        self._closed = False

    # ---- id ----
    def next_id(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}_{self._counters[prefix]:03d}"

    # ---- 默认浏览器 ----
    @property
    def default_handle(self):
        return self._default_handle

    async def ensure_default_browser(self):
        return await self.ensure_browser()

    async def ensure_browser(self, **opts):
        """按需启动/复用默认浏览器；参数一致时幂等，模式由调用方或配置决定。"""
        explicit = any(v is not None for v in opts.values())
        async with self._default_lock:
            current = self._default_handle
            if not explicit:
                if current is None or not current.alive:
                    if current is not None:
                        await self.processes.close(current)
                    current = await self.processes.launch()
                    self._default_handle = current
                return current
            if current is not None and self.processes.matches(current, **opts):
                return current
            if current is not None:
                await self.processes.close(current)
            self._default_handle = await self.processes.launch(**opts)
            return self._default_handle

    # ---- 异步任务调度（事件回调里用）----
    def schedule(self, coro):
        return asyncio.ensure_future(coro)

    # ---- 状态 ----
    async def status(self):
        os_browsers = await asyncio.to_thread(self.processes.os_browsers)
        return {
            "processes": self.processes.list(),
            "os_browsers": os_browsers,
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "counts": {
                "sessions": len(self.sessions),
                "contexts": len(self.contexts),
                "pages": len(self.pages),
                "processes": len(self.processes.list()),
            },
            "active_pages": {cid: c.active_page_id
                             for cid, c in self.contexts.items()},
            "events": self.events.stats(),
            "metrics": self.metrics.snapshot(),
            "domain_restricted": self.policies.domain_restricted,
            "runs": {rid: {"status": r.status, "template_id": r.template_id}
                     for rid, r in self.runs.items()},
            "recording": self.recorder.active,
        }

    # ---- 生命周期 ----
    async def soft_reset(self):
        """清理所有 session/context/page 与运行期计数，保留浏览器进程。

        仅供测试与同一进程内的重复初始化；生产路径用 shutdown()。
        """
        for pid in list(self.pages):
            try:
                await self.page_mgr.close(pid)
            except Exception:
                pass
        for sid in list(self.sessions):
            try:
                await self.session_mgr.close(sid)
            except Exception:
                pass
        for cid in list(self.contexts):
            ctx = self.contexts.get(cid)
            if ctx is not None and ctx.container != "default":
                try:
                    await self.context_mgr.close(cid)
                except Exception:
                    pass
        self.page_mgr.reset_state()
        self.context_mgr.reset_state()
        self.runs.clear()
        self._counters.clear()
        self.events.clear()
        self.metrics.reset()
        try:
            self.recorder.stop()
        except Exception:
            pass

    async def shutdown(self):
        if self._closed:
            return
        self._closed = True
        for pid in list(self.pages):
            try:
                await self.page_mgr.close(pid)
            except Exception:
                pass
        for cid in list(self.contexts):
            try:
                await self.context_mgr.close(cid)
            except Exception:
                pass
        self.page_mgr.reset_state()
        self.context_mgr.reset_state()
        self.sessions.clear()
        self.runs.clear()
        self._default_handle = None
        await self.processes.stop_all()

    def mark_open(self):
        self._closed = False


# ---- 模块级单例 ----
_RUNTIME = None


def get_runtime() -> BrowserRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = BrowserRuntime()
    return _RUNTIME


async def shutdown_runtime():
    global _RUNTIME
    if _RUNTIME is not None:
        await _RUNTIME.shutdown()
        _RUNTIME = None


async def reset_runtime(keep_browser=False) -> BrowserRuntime:
    """仅供测试：重置运行时。

    keep_browser=True 时只清 session/context/page 与计数，保留浏览器进程，
    避免每个用例都冷启动一次 Chrome；进程退出时由调用方统一回收。
    """
    global _RUNTIME
    if _RUNTIME is not None:
        try:
            if keep_browser:
                await _RUNTIME.soft_reset()
                return _RUNTIME
            await _RUNTIME.shutdown()
        except Exception:
            pass
    _RUNTIME = BrowserRuntime()
    return _RUNTIME
