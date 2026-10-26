"""页面生命周期工具。"""

from ..runtime import get_runtime
from .common import backend, guard
from .common import resolve_context as _resolve_context


def register_page_lifecycle_tools(mcp):
    rt_get = get_runtime
    be = backend()

    @mcp.tool()
    @guard
    async def page_open(context_id: str = None, session_id: str = None, url: str = None):
        """新开一个 page（标签页），可选直接导航 URL。"""
        rt = rt_get()
        ctx = await _resolve_context(context_id, session_id)
        handle = await rt.page_mgr.open(ctx, url)
        return {"status": "ok", **handle.to_dict()}

    @mcp.tool()
    @guard
    async def page_list():
        """列出所有 page。"""
        rt = rt_get()
        return {"status": "ok", "items": [p.to_dict() for p in rt.pages.values()]}

    @mcp.tool()
    @guard
    async def page_get(page_id: str):
        """取单个 page 详情。"""
        rt = rt_get()
        return {"status": "ok", **rt.page_mgr.get(page_id).to_dict()}

    @mcp.tool()
    @guard
    async def page_select(page_id: str):
        """选中 active page（省略 page_id 的后续调用默认用它）。"""
        rt = rt_get()
        handle = rt.page_mgr.get(page_id)
        handle.context.active_page_id = page_id
        return {"status": "ok", **handle.to_dict()}

    @mcp.tool()
    @guard
    async def page_close(page_id: str):
        """关闭 page。"""
        rt = rt_get()
        return {"status": "ok", **await rt.page_mgr.close(page_id)}

    @mcp.tool()
    @guard
    async def page_wait(page_id: str = None, state: str = "load", timeout_s: float = 30.0):
        """等待页面加载状态（load / domcontentloaded / networkidle）。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_wait",
                lambda: be.wait_load_state(handle, state, timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }

    @mcp.tool()
    @guard
    async def page_reload(page_id: str = None, timeout_s: float = 30.0):
        """重新加载页面。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_reload",
                lambda: be.reload(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }

    @mcp.tool()
    @guard
    async def page_go_back(page_id: str = None, timeout_s: float = 30.0):
        """后退。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_go_back",
                lambda: be.go_back(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }

    @mcp.tool()
    @guard
    async def page_go_forward(page_id: str = None, timeout_s: float = 30.0):
        """前进。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {
            "status": "ok",
            **await handle.actor.submit(
                "page_go_forward",
                lambda: be.go_forward(handle, timeout_s=timeout_s),
                timeout_s=timeout_s + 5,
            ),
        }
