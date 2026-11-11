"""页面生命周期工具。"""

from ..runtime import get_runtime
from .common import backend
from .common import resolve_context as _resolve_context


def register_page_lifecycle_tools(catalog):
    rt_get = get_runtime

    @catalog.operation(returns="Page")
    async def page_open(context_id: str = None, session_id: str = None, url: str = None):
        """"""
        rt = rt_get()
        ctx = await _resolve_context(context_id, session_id)
        handle = await rt.page_mgr.open(ctx, url)
        return {"status": "ok", **handle.to_dict()}

    @catalog.operation(returns="Pages")
    async def page_list():
        """"""
        rt = rt_get()
        return {"status": "ok", "items": [p.to_dict() for p in rt.pages.values()]}

    @catalog.operation(returns="Page")
    async def page_get(page_id: str):
        """"""
        rt = rt_get()
        return {"status": "ok", **rt.page_mgr.get(page_id).to_dict()}

    @catalog.operation(returns="Page")
    async def page_select(page_id: str):
        "设置active page；省略page_id时按现有上下文解析，歧义报错。"
        rt = rt_get()
        handle = rt.page_mgr.get(page_id)
        handle.context.active_page_id = page_id
        return {"status": "ok", **handle.to_dict()}

    @catalog.operation(returns="PageClosed")
    async def page_close(page_id: str):
        """"""
        rt = rt_get()
        return {"status": "ok", **await rt.page_mgr.close(page_id)}

    @catalog.operation(returns="Location")
    async def page_reload(page_id: str = None, timeout_s: float = 30.0):
        """"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_reload",
                lambda: backend().reload(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }

    @catalog.operation(returns="Location")
    async def page_go_back(page_id: str = None, timeout_s: float = 30.0):
        """"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_go_back",
                lambda: backend().go_back(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }

    @catalog.operation(returns="Location")
    async def page_go_forward(page_id: str = None, timeout_s: float = 30.0):
        """"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_go_forward",
                lambda: backend().go_forward(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }
