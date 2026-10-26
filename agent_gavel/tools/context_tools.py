"""容器工具。"""

from ..core.errors import GavelError
from ..runtime import get_runtime
from .common import guard
from .common import resolve_session as _resolve_session


def register_context_tools(mcp):
    rt_get = get_runtime

    @mcp.tool()
    @guard
    async def context_create(
        session_id: str = None,
        locale: str = None,
        timezone_id: str = None,
        viewport: dict = None,
        device: str = None,
        permissions: list = None,
        extra_http_headers: dict = None,
        storage_state: dict = None,
        container: str = "isolated",
    ):
        """在指定 session 下新建容器（不同账号/cookie/语言/设备）。

        container 默认 isolated：新建隔离容器（继承 profile 的 cookie，但不写回）；
        传 "default" 则复用 profile 默认容器。
        """
        rt = rt_get()
        session = await _resolve_session(session_id)
        config = dict(
            locale=locale,
            timezone_id=timezone_id,
            viewport=viewport,
            device=device,
            permissions=permissions,
            extra_http_headers=extra_http_headers,
            storage_state=storage_state,
            container=container,
        )
        ctx = await rt.context_mgr.create(session, config)
        return {"status": "ok", **ctx.to_dict()}

    @mcp.tool()
    @guard
    async def context_close(context_id: str):
        """关闭 context（级联关闭其 page）。"""
        rt = rt_get()
        return {"status": "ok", **await rt.context_mgr.close(context_id)}

    @mcp.tool()
    @guard
    async def context_cookies_get(context_id: str, url: str = None):
        """读取 context 的 cookie（可选按 url 过滤）。

        ⚠ 隐私敏感：cookie 含登录态。仅在用户授权下读取，不要写入日志/外传。
        """
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        cookies = (
            await ctx.browser_context.cookies(url) if url else await ctx.browser_context.cookies()
        )
        return {"status": "ok", "context_id": context_id, "cookies": cookies, "count": len(cookies)}

    @mcp.tool()
    @guard
    async def context_cookies_set(context_id: str, cookies: list = None):
        """写入 cookie（Playwright addCookies 格式）。

        cookie 需有 url 或 domain+path；url 缺路径会自动补 "/"
        （Playwright 拒绝无 path 的 url，如 "https://example.com"）。
        """
        from urllib.parse import urlparse

        def _norm(c):
            d = dict(c)
            u = d.get("url")
            if u and not urlparse(u).path:
                d["url"] = u.rstrip("/") + "/"
            return d

        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        if not cookies:
            raise GavelError("bad_args", "需要 cookies")
        await ctx.browser_context.add_cookies([_norm(c) for c in cookies])
        return {"status": "ok", "context_id": context_id, "added": len(cookies)}

    @mcp.tool()
    @guard
    async def context_cookies_clear(context_id: str):
        """清空 context 的所有 cookie。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.clear_cookies()
        return {"status": "ok", "context_id": context_id, "cleared": True}

    @mcp.tool()
    @guard
    async def context_set_headers(context_id: str, headers: dict = None):
        """设置 context 的额外请求头（set_extra_http_headers）。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.set_extra_http_headers(headers or {})
        return {"status": "ok", "context_id": context_id, "headers": headers or {}}

    @mcp.tool()
    @guard
    async def context_set_permissions(
        context_id: str, permissions: list = None, origin: str = None
    ):
        """授予/清空 context 权限（给出 permissions 授予，空则清空）。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        if permissions:
            await ctx.browser_context.grant_permissions(permissions, origin=origin)
        else:
            await ctx.browser_context.clear_permissions()
        return {"status": "ok", "context_id": context_id, "permissions": permissions or []}
