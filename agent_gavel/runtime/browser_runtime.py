"""顶层运行时单例（docs/tech-plan.md §5.3）。

持有全部注册表与生命周期：processes / contexts / pages，
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
from .recorder import Recorder
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

        self.contexts = {}
        self.pages = {}
        self.runs = {}
        self._counters = defaultdict(int)

        self.context_mgr = ContextManager(self)
        self.page_mgr = PageManager(self)

        self._default_handle = None
        self._default_lock = asyncio.Lock()
        self._closed = False
        self.shutdown_hook = None

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
        """按资料目录启动/复用浏览器；不同 profile 各自常驻，不互相驱逐。"""
        from .profiles import resolve_dir

        udd = resolve_dir(
            explicit=opts.pop("user_data_dir", None), profile=opts.pop("profile", None)
        )
        async with self._default_lock:
            handle = await self.processes.ensure(udd, **opts)
            if self._default_handle is None or udd == resolve_dir():
                self._default_handle = handle
            return handle

    # ---- 异步任务调度（事件回调里用）----
    def schedule(self, coro):
        return asyncio.ensure_future(coro)

    # ---- 状态 ----
    async def status(self, detail="full"):
        processes = self.processes.list()
        result = {
            "processes": processes,
            "counts": {
                "contexts": len(self.contexts),
                "pages": len(self.pages),
                "processes": len(processes),
            },
            "active_pages": {cid: c.active_page_id for cid, c in self.contexts.items()},
            "domain_restricted": self.policies.domain_restricted,
            "runs": {
                rid: {"status": r.status, "template_id": r.template_id}
                for rid, r in self.runs.items()
            },
            "recording": self.recorder.active,
        }
        try:
            from . import profiles as _profiles

            pdir = self.processes.profile_dir()
            result["profile"] = {
                "name": _profiles.DEFAULT_PROFILE,
                "dir": pdir,
                "default": pdir == _profiles.default_profile_dir(),
            }
        except Exception:
            pass  # silent-ok: 资料信息失败不影响状态查询
        if detail == "full":
            result.update(
                os_browsers=await asyncio.to_thread(self.processes.os_browsers),
                events=self.events.stats(),
                metrics=self.metrics.snapshot(),
            )
        return result

    # ---- 生命周期 ----
    async def soft_reset(self):
        """清理所有 context/page 与运行期计数，保留浏览器进程。

        仅供测试与同一进程内的重复初始化；生产路径用 shutdown()。
        """
        for pid in list(self.pages):
            try:
                await self.page_mgr.close(pid)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
        for cid in list(self.contexts):
            ctx = self.contexts.get(cid)
            if ctx is not None and ctx.container != "default":
                try:
                    await self.context_mgr.close(cid)
                except Exception:
                    pass  # silent-ok: best-effort，失败不影响主流程
        self.page_mgr.reset_state()
        self.context_mgr.reset_state()
        self.runs.clear()
        self._counters.clear()
        self.events.clear()
        self.metrics.reset()
        try:
            self.recorder.stop()
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程

    async def shutdown(self, keep_browsers=False):
        if self._closed:
            return
        self._closed = True
        for pid in list(self.pages):
            try:
                await self.page_mgr.close(pid)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
        for cid in list(self.contexts):
            try:
                await self.context_mgr.close(cid)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
        self.page_mgr.reset_state()
        self.context_mgr.reset_state()
        self.runs.clear()
        self._default_handle = None
        if keep_browsers:
            # 只断开 Playwright 连接，受管 Chrome 继续常驻；下次启动由
            # _reconcile 读 state 文件重新接上，登录态与已开页面都不丢。
            await self.processes.disconnect_all()
        else:
            await self.processes.stop_all()

    async def close_profile(self, profile):
        """只关掉某个资料目录的浏览器，其它 profile 不受影响。

        浏览器消失后，它名下的 context/page 也必须一起摘掉，否则会留下
        指向已关闭浏览器的悬空 context。
        """
        from .profiles import resolve_dir

        udd = resolve_dir(profile=profile)
        key = self.processes._udd_key(udd)
        handle = self.processes._by_udd.get(key)
        if handle is None:
            return False
        await self.processes.close(handle)
        self.processes._by_udd.pop(key, None)
        self._drop_handle_state(handle)
        if self._default_handle is handle:
            self._default_handle = None
        return True

    def _drop_handle_state(self, handle):
        """摘掉某个浏览器句柄名下的 context 与 page 注册。"""
        dropped = {
            cid for cid, ctx in self.contexts.items() if ctx.browser_handle is handle
        }
        for cid in dropped:
            ctx = self.contexts.pop(cid, None)
            if ctx is not None:
                ctx.detach()
                self.context_mgr._by_bc.pop(id(ctx.browser_context), None)
        for pid, ph in list(self.pages.items()):
            if ph.context.context_id in dropped:
                self.pages.pop(pid, None)
                self.page_mgr._by_key.pop(id(ph.page), None)

    def mark_open(self):
        self._closed = False


# ---- 模块级单例 ----
_RUNTIME = None


def get_runtime() -> BrowserRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = BrowserRuntime()
    return _RUNTIME


async def shutdown_runtime(keep_browsers=False):
    global _RUNTIME
    if _RUNTIME is not None:
        await _RUNTIME.shutdown(keep_browsers=keep_browsers)
        _RUNTIME = None


async def reset_runtime(keep_browser=False) -> BrowserRuntime:
    """仅供测试：重置运行时。

    keep_browser=True 时只清 context/page 与计数，保留浏览器进程，
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
            pass  # silent-ok: best-effort，失败不影响主流程
    _RUNTIME = BrowserRuntime()
    return _RUNTIME
