"""M2：locator_* 页面动作 MCP 工具（docs/tech-plan.md §8）。

动作 + 可选断言一次调用（三态）；默认 strict locator（多重命中报错给候选）。
"""

from ..runtime.errors import GavelError
from .common import backend, guard, ids, run_with_optional_assert


def _rt_and_handle(page_id):
    from ..runtime import get_runtime
    rt = get_runtime()
    return rt, rt.page_mgr.resolve(page_id)


def register_action_tools(mcp):
    @mcp.tool()
    @guard
    async def locator_click(page_id: str = None, target: dict = None,
                            button: str = "left", count: int = 1,
                            force: bool = False, wait_navigation: bool = False,
                            timeout_s: float = 30.0, assertions: dict = None,
                            wait_s: float = 6.0):
        """点击元素。target 见 locator 规范；count=2 双击，button=right 右键。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "click",
            lambda: be.act(handle.page, target, "click", button=button,
                           count=count, force=force,
                           wait_navigation=wait_navigation, timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_fill(page_id: str = None, target: dict = None,
                           value: str = "", timeout_s: float = 30.0,
                           assertions: dict = None, wait_s: float = 6.0):
        """清空并填入文本（真实输入）。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "fill",
            lambda: be.act(handle.page, target, "fill", value=value,
                           timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_type(page_id: str = None, target: dict = None,
                           text: str = "", delay_ms: int = 0,
                           timeout_s: float = 30.0, assertions: dict = None,
                           wait_s: float = 6.0):
        """逐键输入文本（含中文/emoji；delay_ms 控制按键间隔）。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "type",
            lambda: be.act(handle.page, target, "type", value=text,
                           delay_ms=delay_ms, timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_press(page_id: str = None, target: dict = None,
                            keys: str = "Enter", timeout_s: float = 30.0,
                            wait_navigation: bool = False,
                            assertions: dict = None, wait_s: float = 6.0):
        """对元素按键/组合键，如 Enter / Tab / Control+A / Shift+Tab。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "press",
            lambda: be.act(handle.page, target, "press", keys=keys,
                           timeout_s=timeout_s,
                           wait_navigation=wait_navigation),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_hover(page_id: str = None, target: dict = None,
                            timeout_s: float = 30.0, assertions: dict = None,
                            wait_s: float = 6.0):
        """悬停元素（触发 tooltip/hover 态）。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "hover",
            lambda: be.act(handle.page, target, "hover", timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_focus(page_id: str = None, target: dict = None,
                            timeout_s: float = 30.0, assertions: dict = None,
                            wait_s: float = 6.0):
        """聚焦元素（不清空）。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "focus",
            lambda: be.act(handle.page, target, "focus", timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_clear(page_id: str = None, target: dict = None,
                            timeout_s: float = 30.0, assertions: dict = None,
                            wait_s: float = 6.0):
        """清空元素内容。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "clear",
            lambda: be.act(handle.page, target, "clear", timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s)

    @mcp.tool()
    @guard
    async def locator_scroll(page_id: str = None, target: dict = None,
                             direction: str = "down", amount: float = 1.0,
                             timeout_s: float = 30.0, assertions: dict = None,
                             wait_s: float = 6.0):
        """滚动：给 target 则滚到该元素可见；否则按 direction/amount 滚页面。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        if target:
            fn = lambda: be.act(handle.page, target, "scroll",  # noqa: E731
                                timeout_s=timeout_s)
        else:
            fn = lambda: be.page_scroll(handle.page, direction=direction,  # noqa: E731
                                        amount=amount, timeout_s=timeout_s)
        return await run_with_optional_assert(
            rt, handle, "scroll", fn, assertions=assertions, wait_s=wait_s,
            timeout_s=timeout_s)
