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
