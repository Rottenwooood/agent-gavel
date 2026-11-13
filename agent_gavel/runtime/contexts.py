"""BrowserContext 资源（docs/tech-plan.md §5.5）。

一个 context = 一个隔离面（cookie/storage/locale/device）。context 创建后
自动登记一只 about:blank 页面作为 active page，使"省略 page_id"立刻可用。

容器策略（对应 profile 资源）：
- **default**（默认）：直接用浏览器资料目录的**默认容器**。它读写的就是 profile
  本体，因此**继承 profile 的登录/cookie，且改动会累积回 profile**（可迭代）。
- **isolated**（可选）：新建隔离容器，**继承 profile 的 cookie**（把资料里的 cookie
  注入进来），但**不写回 profile**（关掉即弃）。用于并行/多账号而不污染身份底座。
"""

import asyncio
from urllib.parse import urlparse

from ..core.errors import GavelError

_CONTEXT_KEYS = (
    "locale",
    "timezone_id",
    "viewport",
    "extra_http_headers",
    "permissions",
    "geolocation",
    "user_agent",
    "color_scheme",
)


class Context:
    def __init__(self, context_id, session, browser_context, container="default"):
        self.context_id = context_id
        self.creator_session_id = session.session_id
        self.browser_handle = session.handle
        self.browser_context = browser_context
        self.container = container  # default | isolated
        self.page_ids = []
        self.active_page_id = None
        self.session_ids = {session.session_id}
        self._run_owner = None
        self._run_lock = asyncio.Lock()
        self._page_handler = None

    def detach(self):
        """移除挂在底层 context 上的 page 监听，避免旧 context 抢注册新页面。"""
        handler = self._page_handler
        self._page_handler = None
        if handler is not None:
            try:
                self.browser_context.remove_listener("page", handler)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    def lease_session_id(self):
        """只有一个租约时返回它；多个会话共享时返回 None。"""
        if len(self.session_ids) == 1:
            return next(iter(self.session_ids))
        return None

    async def acquire_run(self, run_id):
        async with self._run_lock:
            if self._run_owner is not None and self._run_owner != run_id:
                raise GavelError(
                    "context_busy",
                    f"context {self.context_id} 正被 {self._run_owner} 占用",
                    detail={"owner": self._run_owner},
                    hint="同一容器上的 run 需串行；等它结束或换一个 context",
                )
            self._run_owner = run_id

    def release_run(self, run_id):
        if self._run_owner == run_id:
            self._run_owner = None

    def to_dict(self):
        return {
            "context_id": self.context_id,
            "creator_session_id": self.creator_session_id,
            "session_ids": sorted(self.session_ids),
            "container": self.container,
            "page_ids": list(self.page_ids),
            "active_page_id": self.active_page_id,
            "busy_owner": self._run_owner,
        }


class ContextManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self._by_bc = {}  # id(browser_context) -> Context（复用默认容器）
        self._defaults = {}  # id(browser) -> (browser, Context)
        self._guarded = []  # 已安装域名守卫的 browser_context

    def reset_state(self):
        for ctx in list(self.runtime.contexts.values()):
            ctx.detach()
        self._by_bc.clear()
        self._defaults.clear()
        self.runtime.contexts.clear()

    def get(self, context_id):
        ctx = self.runtime.contexts.get(context_id)
        if ctx is None:
            raise GavelError(
                "context_not_found",
                f"未知 context：{context_id}",
                hint="用 context(op=create) 新建",
            )
        return ctx

    def _new_context_kwargs(self, config):
        kw = {}
        for k in _CONTEXT_KEYS:
            if config.get(k) is not None:
                kw[k] = config[k]
        device = config.get("device")
        if device:
            dev = self.runtime.processes.devices.get(device)
            if dev:
                kw.update(dev)
            else:
                raise GavelError("bad_device", f"未知 device：{device}")
        if config.get("storage_state"):
            kw["storage_state"] = config["storage_state"]
        return kw

    def _default_bc(self, session):
        browser = getattr(session.handle, "browser", None)
        if browser is None:
            return None
        try:
            return browser.contexts[0] if browser.contexts else None
        except Exception:
            return None

    async def _seed_from_profile(self, dst_bc, src_bc):
        """把资料目录默认容器的 cookie 注入隔离容器（继承登录态，但不写回）。"""
        if src_bc is None or src_bc is dst_bc:
            return 0
        try:
            cookies = await src_bc.cookies()
        except Exception:
            return 0
        if cookies:
            try:
                await dst_bc.add_cookies(cookies)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
        return len(cookies)

    async def create(self, session, config, *, context_id=None, storage_state=None):
        cid = context_id or self.runtime.next_id("ctx")
        container = (config.get("container") or "default").strip().lower()
        default_bc = self._default_bc(session)
        browser = getattr(session.handle, "browser", None)
        # 没有可用的 profile 默认容器时，default 无意义，按隔离处理（避免容器泄漏）
        if container == "default" and (default_bc is None or browser is None):
            container = "isolated"

        if container == "default":
            # 复用同一个 profile 默认容器（跨会话共享，读写直达 profile）
            key = id(browser)
            hit = self._defaults.get(key)
            if hit is not None and hit[0] is browser:
                existing = hit[1]
                existing.session_ids.add(session.session_id)
                if existing.context_id not in session.context_ids:
                    session.context_ids.append(existing.context_id)
                if not existing.page_ids or existing.active_page_id is None:
                    await self.runtime.page_mgr.open(existing)
                return existing
            ctx = await self.adopt(session, default_bc, context_id=cid, container="default")
            self._defaults[key] = (browser, ctx)
            return ctx

        # 隔离容器（或没有可用的默认容器）
        kw = self._new_context_kwargs(config)
        if "user_agent" not in kw:
            ua = (getattr(session.handle, "opts", None) or {}).get("default_user_agent")
            if ua:
                kw["user_agent"] = ua
        if storage_state is not None:
            kw["storage_state"] = storage_state
        browser = session.handle.browser
        bc = await browser.new_context(**kw)
        ctx = await self.adopt(session, bc, context_id=cid, container="isolated")
        await self._seed_from_profile(bc, default_bc)
        return ctx

    async def adopt(self, session, browser_context, *, context_id=None, container="default"):
        cid = context_id or self.runtime.next_id("ctx")
        ctx = Context(cid, session, browser_context, container=container)
        self.runtime.contexts[cid] = ctx
        self._by_bc[id(browser_context)] = ctx
        session.context_ids.append(cid)
        # 域名白名单：拦截 popup / target=_blank / window.open / 重定向的顶层导航
        if self.runtime.policies.domain_restricted:
            await self._install_domain_guard(ctx)
        handler = self._on_new_page(ctx)
        ctx._page_handler = handler
        browser_context.on("page", handler)
        pages = browser_context.pages
        if pages:
            await self.runtime.page_mgr.register(ctx, pages[0], activate=True)
        else:
            await self.runtime.page_mgr.open(ctx)
        return ctx

    async def _install_domain_guard(self, ctx):
        """在 context 层拦截顶层导航到非白名单域名的请求。"""
        rt = self.runtime
        if any(bc is ctx.browser_context for bc in self._guarded):
            return

        async def _guard(route, request):
            try:
                if request.is_navigation_request():
                    frame = request.frame
                    if (
                        frame is not None
                        and frame == frame.page.main_frame
                        and not rt.policies.is_domain_allowed(request.url)
                    ):
                        page_ctx = rt.page_mgr.find_by_pw_page(frame.page)
                        rt.events.emit(
                            {
                                "event": "policy_blocked",
                                "session_id": page_ctx.lease_session_id() if page_ctx else None,
                                "context_id": page_ctx.context_id if page_ctx else None,
                                "url": request.url,
                                "host": (urlparse(request.url).hostname or "").lower(),
                            }
                        )
                        await route.abort()
                        return
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
            try:
                await route.continue_()
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

        try:
            await ctx.browser_context.route("**/*", _guard)
            self._guarded.append(ctx.browser_context)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程

    def _on_new_page(self, ctx):
        def handler(pw_page):
            try:
                self.runtime.schedule(self.runtime.page_mgr.register(ctx, pw_page))
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

        return handler

    async def close(self, context_id, session_id=None):
        ctx = self.get(context_id)
        leases = {session_id} if ctx.container == "default" and session_id else set(ctx.session_ids)
        for sid in leases:
            ctx.session_ids.discard(sid)
            session = self.runtime.sessions.get(sid)
            if session is not None and context_id in session.context_ids:
                session.context_ids.remove(context_id)
        # 默认容器 = profile 本体：不关闭（保留窗口与磁盘身份），只解绑会话，便于复用与累积
        if ctx.container == "default":
            return {
                "context_id": context_id,
                "detached": True,
                "container": "default",
                "leases": len(ctx.session_ids),
            }
        for pid in list(ctx.page_ids):
            await self.runtime.page_mgr.close(pid, already_closed=True)
        try:
            await asyncio.wait_for(ctx.browser_context.close(), timeout=5)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        self.runtime.contexts.pop(context_id, None)
        self._by_bc.pop(id(ctx.browser_context), None)
        return {"context_id": context_id, "closed": True, "container": "isolated"}
