"""M2：locator_* 页面动作 MCP 工具（docs/tech-plan.md §8）。

动作 + 可选断言一次调用（三态）；默认 strict locator（多重命中报错给候选）。
"""

from ..runtime.errors import GavelError
from .common import backend, guard, ids, rec, run_with_optional_assert


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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("click", target, button=button))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("fill", target, value=value))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("type", target, text=text))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("press", target, keys=keys))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("hover", target))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("focus", target))

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
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("clear", target))

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
            timeout_s=timeout_s,
            record=rec("scroll", target, direction=direction, amount=amount))

    @mcp.tool()
    @guard
    async def locator_check(page_id: str = None, target: dict = None,
                            timeout_s: float = 30.0, assertions: dict = None,
                            wait_s: float = 6.0):
        """勾选 checkbox/radio。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "check",
            lambda: be.act(handle.page, target, "check", timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("check", target))

    @mcp.tool()
    @guard
    async def locator_uncheck(page_id: str = None, target: dict = None,
                              timeout_s: float = 30.0, assertions: dict = None,
                              wait_s: float = 6.0):
        """取消勾选 checkbox。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "uncheck",
            lambda: be.act(handle.page, target, "uncheck", timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("uncheck", target))

    @mcp.tool()
    @guard
    async def locator_select(page_id: str = None, target: dict = None,
                             value: str = None, select_by: str = "value",
                             timeout_s: float = 30.0, assertions: dict = None,
                             wait_s: float = 6.0):
        """选择 <select> 选项。select_by: value|label|index。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "select",
            lambda: be.act(handle.page, target, "select", value=value,
                           select_by=select_by, timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("select", target, value=value, select_by=select_by))

    @mcp.tool()
    @guard
    async def locator_drag(page_id: str = None, target: dict = None,
                           destination: dict = None, timeout_s: float = 30.0,
                           assertions: dict = None, wait_s: float = 6.0):
        """拖拽：把 target 拖到 destination。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        return await run_with_optional_assert(
            rt, handle, "drag",
            lambda: be.act(handle.page, target, "drag",
                           destination=destination, timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("drag", target, destination=destination))

    @mcp.tool()
    @guard
    async def locator_upload(page_id: str = None, target: dict = None,
                             files: list = None, timeout_s: float = 30.0,
                             assertions: dict = None, wait_s: float = 6.0):
        """给 <input type=file> 设置文件（受文件白名单约束）。"""
        rt, handle = _rt_and_handle(page_id)
        if not files:
            raise GavelError("bad_args", "需要 files（路径或路径列表）")
        for p in (files if isinstance(files, list) else [files]):
            rt.policies.check_local_path(p, purpose="上传")
        be = backend()
        payload = files if isinstance(files, list) else [files]
        return await run_with_optional_assert(
            rt, handle, "upload",
            lambda: be.act(handle.page, target, "upload", files=payload,
                           timeout_s=timeout_s),
            assertions=assertions, wait_s=wait_s, timeout_s=timeout_s,
            record=rec("upload", target, files=payload))

    @mcp.tool()
    @guard
    async def locator_set_files(page_id: str = None, target: dict = None,
                                files: list = None, timeout_s: float = 30.0,
                                assertions: dict = None, wait_s: float = 6.0):
        """同 locator_upload：给文件输入框设置文件。"""
        return await locator_upload(page_id=page_id, target=target,
                                    files=files, timeout_s=timeout_s,
                                    assertions=assertions, wait_s=wait_s)

    @mcp.tool()
    @guard
    async def page_handle_dialog(page_id: str = None, action: str = "accept",
                                 prompt_text: str = None, policy: str = None):
        """处理页面对话框（alert/confirm/prompt）。

        action: accept|dismiss；prompt_text 用于 prompt。
        policy: manual|auto_accept|auto_dismiss —— 设置后续 dialog 的默认行为。
        该调用**不经页面动作队列**（避免与阻塞动作死锁）。

        注意：policy=manual（默认）时 dialog 会阻塞页面，须再调本工具处理。
        """
        rt, handle = _rt_and_handle(page_id)
        return {"status": "ok", **ids(handle), **await backend().handle_dialog(
            handle, action=action, prompt_text=prompt_text, policy=policy)}
