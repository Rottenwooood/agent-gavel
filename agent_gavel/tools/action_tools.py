"""locator_* 页面动作工具：动作 + 可选断言一次调用（三态）。

执行路径统一为 `_run_target`；各工具只保留 MCP 需要的签名与差异参数。
"""

from ..core.errors import GavelError
from .common import backend, ids, rec, run_with_optional_assert


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


def register_action_tools(catalog):
    @catalog.operation(returns="Action", action="click")
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
        follow_popup: bool = True,
    ):
        "count=2双击；follow_popup=False不跟随新弹出的标签页。"
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
            follow_popup=follow_popup,
        )

    @catalog.operation(returns="Action", action="fill")
    async def locator_fill(
        page_id: str = None,
        target: dict = None,
        value: str = "",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "替换输入。"
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

    @catalog.operation(returns="Action", action="type")
    async def locator_type(
        page_id: str = None,
        target: dict = None,
        text: str = "",
        delay_ms: int = 0,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "逐键追加输入。"
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

    @catalog.operation(returns="Action", action="press")
    async def locator_press(
        page_id: str = None,
        target: dict = None,
        keys: str = "Enter",
        timeout_s: float = 30.0,
        wait_navigation: bool = False,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "按键或组合键。"
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

    @catalog.operation(returns="Action", action="hover")
    async def locator_hover(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """"""
        return await _run_target(
            page_id,
            "hover",
            target,
            record=rec("hover", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @catalog.operation(returns="Action", action="focus")
    async def locator_focus(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """"""
        return await _run_target(
            page_id,
            "focus",
            target,
            record=rec("focus", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @catalog.operation(returns="Action", action="clear")
    async def locator_clear(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """"""
        return await _run_target(
            page_id,
            "clear",
            target,
            record=rec("clear", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @catalog.operation(returns="Action", action="scroll")
    async def locator_scroll(
        page_id: str = None,
        target: dict = None,
        direction: str = "down",
        amount: float = 1.0,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "有target滚至元素，否则滚页面。"
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

    @catalog.operation(returns="Action", action="check")
    async def locator_check(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """"""
        return await _run_target(
            page_id,
            "check",
            target,
            record=rec("check", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @catalog.operation(returns="Action", action="uncheck")
    async def locator_uncheck(
        page_id: str = None,
        target: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        """"""
        return await _run_target(
            page_id,
            "uncheck",
            target,
            record=rec("uncheck", target),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
        )

    @catalog.operation(returns="Action", action="select")
    async def locator_select(
        page_id: str = None,
        target: dict = None,
        value: str = None,
        select_by: str = "value",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "value允许null。"
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

    @catalog.operation(returns="Action", action="drag")
    async def locator_drag(
        page_id: str = None,
        target: dict = None,
        destination: dict = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        ""
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

    @catalog.operation(returns="Action", action="upload")
    async def locator_upload(
        page_id: str = None,
        target: dict = None,
        files: list = None,
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
    ):
        "上传文件，受路径规则约束。"
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

    @catalog.operation(returns="Dialog")
    async def page_handle_dialog(
        page_id: str = None,
        action: str = "accept",
        prompt_text: str = None,
        policy: str = None,
    ):
        "action=accept|dismiss；policy=manual|auto_accept|auto_dismiss；prompt_text用于prompt；绕开动作队列避免死锁。"
        rt, handle = _rt_and_handle(page_id)
        return {
            "status": "ok",
            **ids(handle),
            **await backend().handle_dialog(
                handle, action=action, prompt_text=prompt_text, policy=policy
            ),
        }
