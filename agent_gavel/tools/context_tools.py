"""容器工具。"""

import asyncio
import pathlib

from ..core.errors import GavelError
from ..runtime import get_runtime


def register_context_tools(catalog):
    rt_get = get_runtime

    @catalog.operation(returns="Context", fields={"container": {"enum": ["default", "isolated"]}})
    async def context_create(
        profile: str = None,
        locale: str = None,
        timezone_id: str = None,
        viewport: dict = None,
        device: str = None,
        permissions: list = None,
        extra_http_headers: dict = None,
        storage_state: dict = None,
        container: str = "default",
        isolated: bool = None,
    ):
        "container默认isolated，继承profile登录态但不写回；default复用并写回。"
        rt = rt_get()
        config = dict(
            locale=locale,
            timezone_id=timezone_id,
            viewport=viewport,
            device=device,
            permissions=permissions,
            extra_http_headers=extra_http_headers,
            storage_state=storage_state,
            container="isolated" if isolated else container,
        )
        ctx = await rt.context_mgr.ensure(
            profile=profile,
            isolated=(config.get("container") or "isolated") == "isolated",
            config=config,
        )
        return {"status": "ok", **ctx.to_dict()}

    @catalog.operation(returns="ContextClosed")
    async def context_close(context_id: str):
        """"""
        rt = rt_get()
        return {"status": "ok", **await rt.context_mgr.close(context_id)}

    @catalog.operation(returns="Cookies")
    async def context_cookies_get(context_id: str, url: str = None):
        "含登录态，仅在用户授权下读取，不外传。"
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        cookies = (
            await ctx.browser_context.cookies(url) if url else await ctx.browser_context.cookies()
        )
        return {"status": "ok", "context_id": context_id, "cookies": cookies, "count": len(cookies)}

    @catalog.operation(returns="CookiesAdded", required=("cookies",), nonempty=("cookies",))
    async def context_cookies_set(context_id: str, cookies: list = None):
        "cookies为Playwright格式，需url或domain+path；缺URL路径补/。"
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

    @catalog.operation(returns="CookiesCleared")
    async def context_cookies_clear(context_id: str):
        """"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.clear_cookies()
        return {"status": "ok", "context_id": context_id, "cleared": True}

    @catalog.operation(returns="Headers")
    async def context_set_headers(context_id: str, headers: dict = None):
        """"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.set_extra_http_headers(headers or {})
        return {"status": "ok", "context_id": context_id, "headers": headers or {}}

    @catalog.operation(returns="Permissions")
    async def context_set_permissions(
        context_id: str, permissions: list = None, origin: str = None
    ):
        "非空授予；空列表清空权限，origin仅用于授予。"
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        if permissions:
            await ctx.browser_context.grant_permissions(permissions, origin=origin)
        else:
            await ctx.browser_context.clear_permissions()
        return {"status": "ok", "context_id": context_id, "permissions": permissions or []}

    @catalog.operation(returns="StorageExport")
    async def context_export_state(context_id: str, path: str = None):
        "导出 storage state（cookie/localStorage）。"
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        state = await ctx.browser_context.storage_state()
        if path:
            rt.policies.check_local_path(path, purpose="导出 storage state")
            import json

            payload = json.dumps(state, ensure_ascii=False, indent=2)
            await asyncio.to_thread(pathlib.Path(path).write_text, payload, encoding="utf-8")
            return {"status": "ok", "context_id": context_id, "path": path}
        return {"status": "ok", "context_id": context_id, "storage_state": state}

    @catalog.operation(returns="Context", required=("path",))
    async def context_import_state(path: str, profile: str = None):
        "path为可信storage_state文件；导入成新的隔离容器。"
        rt = rt_get()
        rt.policies.check_local_path(path, purpose="导入 storage state")
        ctx = await rt.context_mgr.ensure(
            profile=profile, isolated=True, config={"storage_state": path}
        )
        return {"status": "ok", **ctx.to_dict()}
