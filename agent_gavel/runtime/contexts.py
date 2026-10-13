"""BrowserContext 资源（docs/tech-plan.md §5.5）。

一个 context = 一个隔离面（cookie/storage/locale/device）。context 创建后
自动登记一只 about:blank 页面作为 active page，使"省略 page_id"立刻可用。

容器策略（对应 profile 资源）：
- **default**（默认）：直接用浏览器资料目录的**默认容器**。它读写的就是 profile
  本体，因此**继承 profile 的登录/cookie，且改动会累积回 profile**（可迭代）。
- **isolated**（可选）：新建隔离容器，**继承 profile 的 cookie**（把资料里的 cookie
  注入进来），但**不写回 profile**（关掉即弃）。用于并行/多账号而不污染身份底座。
"""

from urllib.parse import urlparse

from .errors import GavelError

_CONTEXT_KEYS = ("locale", "timezone_id", "viewport", "extra_http_headers",
                 "permissions", "geolocation", "user_agent", "color_scheme")


class Context:
    def __init__(self, context_id, session, browser_context,
                 container="default"):
        self.context_id = context_id
        self.session = session
        self.browser_context = browser_context
        self.container = container          # default | isolated
        self.page_ids = []
        self.active_page_id = None
        self.session_ids = [session.session_id]
        self.refcount = 1

    def to_dict(self):
        return {
            "context_id": self.context_id,
            "session_id": self.session.session_id,
            "session_ids": list(self.session_ids),
            "container": self.container,
            "page_ids": list(self.page_ids),
            "active_page_id": self.active_page_id,
        }


class ContextManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self._by_bc = {}          # id(browser_context) -> Context（复用默认容器）
        self._default_ctx = None  # profile 默认容器对应的 Context
        self._default_browser = None

    def get(self, context_id):
        ctx = self.runtime.contexts.get(context_id)
        if ctx is None:
            raise GavelError("context_not_found", f"未知 context：{context_id}",
                             hint="用 context_create 新建")
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
                pass
        return len(cookies)

    async def create(self, session, config, *, context_id=None,
                     storage_state=None):
        cid = context_id or self.runtime.next_id("ctx")
        container = (config.get("container") or "default").strip().lower()
        default_bc = self._default_bc(session)
        browser = getattr(session.handle, "browser", None)
        # 没有可用的 profile 默认容器时，default 无意义，按隔离处理（避免容器泄漏）
        if container == "default" and (default_bc is None or browser is None):
            container = "isolated"

        if container == "default":
            # 复用同一个 profile 默认容器（跨会话共享，读写直达 profile）
            if self._default_ctx is not None and self._default_browser is browser:
                existing = self._default_ctx
                if session.session_id not in existing.session_ids:
                    existing.session_ids.append(session.session_id)
                    existing.refcount += 1
                if existing.context_id not in session.context_ids:
                    session.context_ids.append(existing.context_id)
                return existing
            ctx = await self.adopt(session, default_bc, context_id=cid,
                                   container="default")
            self._default_ctx = ctx
            self._default_browser = browser
            return ctx

        # 隔离容器（或没有可用的默认容器）
        kw = self._new_context_kwargs(config)
        if "user_agent" not in kw:
            ua = (getattr(session.handle, "opts", None) or {}).get(
                "default_user_agent")
            if ua:
                kw["user_agent"] = ua
        if storage_state is not None:
            kw["storage_state"] = storage_state
        browser = session.handle.browser
        bc = await browser.new_context(**kw)
        ctx = await self.adopt(session, bc, context_id=cid,
                               container="isolated")
        if container != "default":
            await self._seed_from_profile(bc, default_bc)
        return ctx

    async def adopt(self, session, browser_context, *, context_id=None,
                    container="default"):
        cid = context_id or self.runtime.next_id("ctx")
        ctx = Context(cid, session, browser_context, container=container)
        self.runtime.contexts[cid] = ctx
        self._by_bc[id(browser_context)] = ctx
        session.context_ids.append(cid)
        # 域名白名单：拦截 popup / target=_blank / window.open / 重定向的顶层导航
        if self.runtime.policies.domain_restricted:
            await self._install_domain_guard(ctx)
        browser_context.on("page", self._on_new_page(ctx))
        pages = browser_context.pages
        if pages:
            await self.runtime.page_mgr.register(ctx, pages[0])
        else:
            await self.runtime.page_mgr.open(ctx)
        return ctx

    async def _install_domain_guard(self, ctx):
        """在 context 层拦截顶层导航到非白名单域名的请求。"""
        rt = self.runtime

        async def _guard(route, request):
            try:
                if request.is_navigation_request():
                    frame = request.frame
                    if frame is not None and frame == frame.page.main_frame \
                            and not rt.policies.is_domain_allowed(request.url):
                        rt.events.emit({
                            "event": "policy_blocked",
                            "session_id": ctx.session.session_id,
                            "context_id": ctx.context_id,
                            "url": request.url,
                            "host": (urlparse(request.url).hostname or "").lower(),
                        })
                        await route.abort()
                        return
            except Exception:
                pass
            try:
                await route.continue_()
            except Exception:
                pass

        try:
            await ctx.browser_context.route("**/*", _guard)
        except Exception:
            pass

    def _on_new_page(self, ctx):
        def handler(pw_page):
            try:
                self.runtime.schedule(self.runtime.page_mgr.register(ctx, pw_page))
            except Exception:
                pass
        return handler

    async def close(self, context_id, session_id=None):
        ctx = self.get(context_id)
        ctx.refcount -= 1
        if session_id and session_id in ctx.session_ids:
            ctx.session_ids.remove(session_id)
        # 默认容器 = profile 本体：不关闭（保留窗口与磁盘身份），只解绑会话，便于复用与累积
        if ctx.container == "default":
            return {"context_id": context_id, "detached": True,
                    "container": "default", "refcount": max(ctx.refcount, 0)}
        for pid in list(ctx.page_ids):
            await self.runtime.page_mgr.close(pid, already_closed=True)
        try:
            await ctx.browser_context.close()
        except Exception:
            pass
        self.runtime.contexts.pop(context_id, None)
        self._by_bc.pop(id(ctx.browser_context), None)
        return {"context_id": context_id, "closed": True,
                "container": "isolated"}
