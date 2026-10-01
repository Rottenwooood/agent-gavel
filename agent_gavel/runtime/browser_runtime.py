"""顶层运行时单例（docs/tech-plan.md §5.3）。

持有全部注册表与生命周期：processes / sessions / contexts / pages，
以及 events / metrics / policies / artifacts。tools 层只与本类交互。
"""

import asyncio
from collections import defaultdict

from .artifacts import ArtifactStore
from .browser_process import BrowserProcessManager
from .contexts import ContextManager
from .events import EventBus
from .metrics import MetricsRecorder
from .pages import PageManager
from .policies import PolicyManager
from .sessions import Session, SessionManager


class BrowserRuntime:
    def __init__(self):
        self.processes = BrowserProcessManager()
        self.events = EventBus()
        self.metrics = MetricsRecorder()
        self.policies = PolicyManager()
        self.artifacts = ArtifactStore()

        self.sessions = {}
        self.contexts = {}
        self.pages = {}
        self._counters = defaultdict(int)

        self.session_mgr = SessionManager(self)
        self.context_mgr = ContextManager(self)
        self.page_mgr = PageManager(self)

        self.active_page_id = None
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
        async with self._default_lock:
            if self._default_handle is None or not self._default_handle.alive:
                if self._default_handle is not None:
                    await self.processes.close(self._default_handle)
                self._default_handle = await self.processes.launch()
            return self._default_handle

    # ---- 异步任务调度（事件回调里用）----
    def schedule(self, coro):
        return asyncio.ensure_future(coro)

    # ---- 状态 ----
    def status(self):
        return {
            "processes": self.processes.list(),
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "counts": {
                "sessions": len(self.sessions),
                "contexts": len(self.contexts),
                "pages": len(self.pages),
                "processes": len(self.processes.list()),
            },
            "active_page_id": self.active_page_id,
            "events": self.events.stats(),
            "metrics": self.metrics.snapshot(),
            "domain_restricted": self.policies.domain_restricted,
        }

    # ---- 生命周期 ----
    def reset_active_if(self, page_id):
        if self.active_page_id == page_id:
            self.active_page_id = None

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
        self.sessions.clear()
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


def reset_runtime() -> BrowserRuntime:
    """仅供测试：丢弃旧单例，返回一个新的（不负责关旧浏览器）。"""
    global _RUNTIME
    _RUNTIME = BrowserRuntime()
    return _RUNTIME
