"""locator_* 页面动作工具：动作 + 可选断言一次调用（三态）。

执行路径统一为 `_run_target`；各工具只保留 MCP 需要的签名与差异参数。
"""

from ..core.errors import GavelError
from .common import backend, guard, ids, rec, run_with_optional_assert


def _rt_and_handle(page_id):
    from ..runtime import get_runtime

    rt = get_runtime()
    return rt, rt.page_mgr.resolve(page_id)


async def _run_target(
    page_id,
    action,
    target,
    *,
    record,
    assertions,
    wait_s,
    timeout_s,
    invoke=None,
    **kw,
):
    rt, handle = _rt_and_handle(page_id)
    if invoke is None:
        be = backend()

        def invoke(h):
            return be.act(h, target, action, timeout_s=timeout_s, **kw)

    return await run_with_optional_assert(
        rt,
        handle,
        action,
        lambda: invoke(handle),
        assertions=assertions,
        wait_s=wait_s,
        timeout_s=timeout_s,
        record=record,
    )


def register_action_tools(mcp):
    @mcp.tool()
    @guard
    async def locator_click(
        page_id: str = None,
        target: dict = None,
        button: str = "left",
        count: int = 1,
        force: bool = False,
        wait_navigation: bool = False,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """点击元素。target 见 locator 规范；count=2 双击，button=right 右键。"""
        return await _run_target(
            page_id,
            "click",
            target,
            record=rec("click", target, button=button),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            button=button,
            count=count,
            force=force,
            wait_navigation=wait_navigation,
        )

    @mcp.tool()
    @guard
    async def locator_fill(
        page_id: str = None,
        target: dict = None,
        value: str = "",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """清空并填入文本（真实输入）。"""
        return await _run_target(
            page_id,
            "fill",
            target,
            record=rec("fill", target, value=value),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            value=value,
        )

    @mcp.tool()
    @guard
    async def locator_type(
        page_id: str = None,
        target: dict = None,
        text: str = "",
        delay_ms: int = 0,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """逐键输入文本（含中文/emoji；delay_ms 控制按键间隔）。"""
        return await _run_target(
            page_id,
            "type",
            target,
            record=rec("type", target, text=text),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            value=text,
            delay_ms=delay_ms,
        )

    @mcp.tool()
    @guard
    async def locator_press(
        page_id: str = None,
        target: dict = None,
        keys: str = "Enter",
        timeout_s: float = 30.0,
        wait_navigation: bool = False,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """对元素按键/组合键，如 Enter / Tab / Control+A / Shift+Tab。"""
        return await _run_target(
            page_id,
            "press",
            target,
            record=rec("press", target, keys=keys),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            keys=keys,
            wait_navigation=wait_navigation,
        )

    @mcp.tool()
    @guard
    async def locator_hover(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """悬停元素（触发 tooltip/hover 态）。"""
        return await _run_target(
            page_id,
            "hover",
            target,
            record=rec("hover", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    @guard
    async def locator_focus(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """聚焦元素（不清空）。"""
        return await _run_target(
            page_id,
            "focus",
            target,
            record=rec("focus", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    @guard
    async def locator_clear(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """清空元素内容。"""
        return await _run_target(
            page_id,
            "clear",
            target,
            record=rec("clear", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    @guard
    async def locator_scroll(
        page_id: str = None,
        target: dict = None,
        direction: str = "down",
        amount: float = 1.0,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """滚动：给 target 则滚到该元素可见；否则按 direction/amount 滚页面。"""
        rt, handle = _rt_and_handle(page_id)
        be = backend()
        if target:

            def invoke(h):
                return be.act(h, target, "scroll", timeout_s=timeout_s)

        else:

            def invoke(h):
                return be.page_scroll(
                    h,
                    direction=direction,
                    amount=amount,
                    timeout_s=timeout_s,
                )

        return await _run_target(
            page_id,
            "scroll",
            target,
            record=rec("scroll", target, direction=direction, amount=amount),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            invoke=invoke,
        )

    @mcp.tool()
    @guard
    async def locator_check(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """勾选 checkbox/radio。"""
        return await _run_target(
            page_id,
            "check",
            target,
            record=rec("check", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    @guard
    async def locator_uncheck(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """取消勾选 checkbox。"""
        return await _run_target(
            page_id,
            "uncheck",
            target,
            record=rec("uncheck", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    @guard
    async def locator_select(
        page_id: str = None,
        target: dict = None,
        value: str = None,
        select_by: str = "value",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """选择 <select> 选项。select_by: value|label|index。"""
        return await _run_target(
            page_id,
            "select",
            target,
            record=rec("select", target, value=value, select_by=select_by),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            value=value,
            select_by=select_by,
        )

    @mcp.tool()
    @guard
    async def locator_drag(
        page_id: str = None,
        target: dict = None,
        destination: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """拖拽：把 target 拖到 destination。"""
        return await _run_target(
            page_id,
            "drag",
            target,
            record=rec("drag", target, destination=destination),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            destination=destination,
        )

    @mcp.tool()
    @guard
    async def locator_upload(
        page_id: str = None,
        target: dict = None,
        files: list = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """给 <input type=file> 设置文件（受文件白名单约束）。"""
        rt, _handle = _rt_and_handle(page_id)
        if not files:
            raise GavelError("bad_args", "需要 files（路径或路径列表）")
        for path in files if isinstance(files, list) else [files]:
            rt.policies.check_local_path(path, purpose="上传")
        payload = files if isinstance(files, list) else [files]
        return await _run_target(
            page_id,
            "upload",
            target,
            record=rec("upload", target, files=payload),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            files=payload,
        )

    @mcp.tool()
    @guard
    async def page_handle_dialog(
        page_id: str = None,
        action: str = "accept",
        prompt_text: str = None,
        policy: str = None,
    ):
        """处理页面对话框（alert/confirm/prompt）。

        action: accept|dismiss；prompt_text 用于 prompt。
        policy: manual|auto_accept|auto_dismiss —— 设置后续 dialog 的默认行为。
        该调用**不经页面动作队列**（避免与阻塞动作死锁）。

        注意：policy=manual（默认）时 dialog 会阻塞页面，须再调本工具处理。
        """
        rt, handle = _rt_and_handle(page_id)
        return {
            "status": "ok",
            **ids(handle),
            **await backend().handle_dialog(
                handle, action=action, prompt_text=prompt_text, policy=policy
            ),
        }
