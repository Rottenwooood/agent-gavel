"""BrowserContext 资源（docs/tech-plan.md §5.5）。

一个 context = 一个隔离面（cookie/storage/locale/device）。context 创建后
自动登记一只 about:blank 页面作为 active page，使"省略 page_id"立刻可用。
"""

from urllib.parse import urlparse

from .errors import GavelError

_CONTEXT_KEYS = ("locale", "timezone_id", "viewport", "extra_http_headers",
                 "permissions", "geolocation", "user_agent", "color_scheme")


class Context:
    def __init__(self, context_id, session, browser_context):
        self.context_id = context_id
        self.session = session
        self.browser_context = browser_context
        self.page_ids = []
        self.active_page_id = None

    def to_dict(self):
        return {
            "context_id": self.context_id,
            "session_id": self.session.session_id,
            "page_ids": list(self.page_ids),
            "active_page_id": self.active_page_id,
        }


class ContextManager:
    def __init__(self, runtime):
        self.runtime = runtime

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

    async def create(self, session, config, *, context_id=None,
                     storage_state=None):
        cid = context_id or self.runtime.next_id("ctx")
        kw = self._new_context_kwargs(config)
        if storage_state is not None:
            kw["storage_state"] = storage_state
        bc = await session.handle.browser.new_context(**kw)
        return await self.adopt(session, bc, context_id=cid)

    async def adopt(self, session, browser_context, *, context_id=None):
        cid = context_id or self.runtime.next_id("ctx")
        ctx = Context(cid, session, browser_context)
        self.runtime.contexts[cid] = ctx
        session.context_ids.append(cid)
        # 域名白名单：拦截 popup / target=_blank / window.open / 重定向的
        # 顶层导航，防止新页面落到未允许域名（仅限制开启时安装）
        if self.runtime.policies.domain_restricted:
            await self._install_domain_guard(ctx)
        # popup / target=_blank 自动登记
        browser_context.on("page", self._on_new_page(ctx))
        # 初始页面
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

    async def close(self, context_id):
        ctx = self.get(context_id)
        for pid in list(ctx.page_ids):
            await self.runtime.page_mgr.close(pid, already_closed=True)
        try:
            await ctx.browser_context.close()
        except Exception:
            pass
        self.runtime.contexts.pop(context_id, None)
        if ctx.session and context_id in ctx.session.context_ids:
            ctx.session.context_ids.remove(context_id)
        return {"context_id": context_id, "closed": True}
