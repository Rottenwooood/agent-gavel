"""M2：expect / page_wait_for_* MCP 工具（docs/tech-plan.md §8、§10）。

expect 做 UI/业务层断言，三态：pass / fail / ambiguous（有动作前后签名时）。
"""

from ..runtime.errors import GavelError
from .common import backend, guard, ids


def register_assertion_tools(mcp):
    @mcp.tool()
    @guard
    async def expect(page_id: str = None, checks: dict = None,
                     wait_s: float = 6.0):
        """断言一组检查，轮询到满足或超时。

        checks: {名字: {op, value, read?, selector?, attr?, expr?}}
        op: eq|neq|contains|not_contains|regex|exists|not_exists|gt|lt|gte|lte|
            count_eq|count_gte|count_gt
        read: text|value|attr|count|html|url|title|expr（缺省按字段推断）
        返回 status: pass|fail；配动作使用时由动作工具判 ambiguous。
        """
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        if not checks:
            raise GavelError("bad_args", "需要 checks")
        be = backend()
        ev = await be.evaluate_checks(handle.page, checks, wait_s=wait_s)
        return {"status": ev["status"], **ids(handle),
                "assertions": ev["assertions"]}

    @mcp.tool()
    @guard
    async def page_wait_for_url(page_id: str = None, url: str = None,
                                timeout_s: float = 30.0):
        """等待 URL 匹配（子串/glob/正则，Playwright wait_for_url 语义）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        if not url:
            raise GavelError("bad_args", "需要 url")
        be = backend()
        res = await handle.actor.submit(
            "wait_for_url",
            lambda: be.wait_for_url(handle, url, timeout_s=timeout_s),
            timeout_s=timeout_s + 5)
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_wait_for_selector(page_id: str = None, selector: str = None,
                                     state: str = "visible",
                                     timeout_s: float = 30.0):
        """等待选择器出现（state: attached|detached|visible|hidden）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        if not selector:
            raise GavelError("bad_args", "需要 selector")
        be = backend()
        res = await handle.actor.submit(
            "wait_for_selector",
            lambda: be.wait_for_selector(handle, selector, state=state,
                                         timeout_s=timeout_s),
            timeout_s=timeout_s + 5)
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_wait_for_load(page_id: str = None, state: str = "load",
                                 timeout_s: float = 30.0):
        """等待页面加载状态（load / domcontentloaded / networkidle）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "wait_for_load",
            lambda: be.wait_load_state(handle, state, timeout_s),
            timeout_s=timeout_s + 5)
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_wait_for_response(page_id: str = None, url: str = None,
                                     status: int = None,
                                     timeout_s: float = 30.0):
        """等待一个匹配的响应（url 支持子串或 * glob；可选 status）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "wait_for_response",
            lambda: be.wait_for_response(handle, url=url, status=status,
                                         timeout_s=timeout_s),
            timeout_s=timeout_s + 5)
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_wait_for_download(page_id: str = None,
                                     timeout_s: float = 30.0):
        """等待下载完成并返回 artifact（由 page.on('download') 自动捕获）。"""
        from ..runtime import get_runtime
        from ..runtime.errors import GavelError
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        ev = await rt.events.wait_for(
            lambda e: e.get("event") == "download_completed"
            and e.get("page_id") == handle.page_id, timeout_s)
        if ev is None:
            raise GavelError("timeout", "等待下载超时", retryable=True)
        info = rt.artifacts.get(ev["artifact_id"])
        return {"status": "ok", **ids(handle), "artifact_id": ev["artifact_id"],
                "suggested_filename": info["suggested_filename"],
                "size_bytes": info["size_bytes"], "sha256": info["sha256"],
                "mime_type": info["mime_type"]}

    @mcp.tool()
    @guard
    async def page_wait_for_popup(page_id: str = None, url: str = None,
                                  timeout_s: float = 30.0):
        """等待由该 page 打开的新页面（popup / target=_blank）。"""
        from ..runtime import get_runtime
        from ..runtime.errors import GavelError
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)

        def pred(e):
            if e.get("event") != "page_created":
                return False
            if e.get("opener_page_id") != handle.page_id:
                return False
            if url and url not in (e.get("url") or ""):
                return False
            return True

        ev = await rt.events.wait_for(pred, timeout_s)
        if ev is None:
            raise GavelError("timeout", "等待 popup 超时", retryable=True)
        new_page = rt.pages.get(ev["page_id"])
        return {"status": "ok", **ids(handle), "new_page_id": ev["page_id"],
                "opener_page_id": ev.get("opener_page_id"),
                "url": ev.get("url"),
                "new_page": new_page.to_dict() if new_page else None}

    @mcp.tool()
    @guard
    async def page_wait_for_event(kinds: list = None, page_id: str = None,
                                  timeout_s: float = 30.0,
                                  include_past: bool = False):
        """等待一个**新**事件（kinds 为事件名列表，如 ["download_completed"]）。

        默认只等调用之后发生的事件（避免命中缓冲里的旧事件）；
        include_past=True 时也接受缓冲里的历史事件。
        """
        import time
        from ..runtime import get_runtime
        from ..runtime.errors import GavelError
        rt = get_runtime()
        allowed = set(kinds) if kinds else None

        def pred(e):
            if allowed is not None and e.get("event") not in allowed:
                return False
            if page_id and e.get("page_id") not in (page_id, None):
                return False
            return True

        since = None if include_past else time.time()
        ev = await rt.events.wait_for(pred, timeout_s, since=since)
        if ev is None:
            raise GavelError("timeout", "等待事件超时", retryable=True)
        return {"status": "ok", "event": ev}
