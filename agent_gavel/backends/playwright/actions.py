"""目标型动作的具体实现。

每个函数签名统一为 (backend, handle, target, *, timeout_s, **kw)，
由 PlaywrightBackend.act 按动作类型分发。
"""

from ...core.errors import GavelError


def _done(handle, action, url_before, *, wait_navigation=False, timeout_s=30.0):
    result = {
        "action": action,
        "action_completed": True,
        "url_before": url_before,
        "url_after": handle.page.url,
    }
    return result


async def do_click(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.click(
        button=kw.get("button", "left"),
        click_count=kw.get("count", 1),
        timeout=timeout_s * 1000,
        force=kw.get("force", False),
    )
    return _done(handle, "click", url_before)


async def do_fill(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.fill(kw.get("value", ""), timeout=timeout_s * 1000)
    return _done(handle, "fill", url_before)


async def do_type(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    text = kw.get("value", "")
    delay = kw.get("delay_ms", 0)
    try:
        await loc.press_sequentially(text, delay=delay, timeout=timeout_s * 1000)
    except AttributeError:
        await loc.type(text, delay=delay, timeout=timeout_s * 1000)
    return _done(handle, "type", url_before)


async def do_press(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.press(kw.get("keys", "Enter"), timeout=timeout_s * 1000)
    result = _done(handle, "press", url_before)
    if kw.get("wait_navigation"):
        try:
            await handle.page.wait_for_load_state("domcontentloaded", timeout=timeout_s * 1000)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        result["url_after"] = handle.page.url
    return result


async def do_hover(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.hover(timeout=timeout_s * 1000)
    return _done(handle, "hover", url_before)


async def do_focus(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.focus(timeout=timeout_s * 1000)
    return _done(handle, "focus", url_before)


async def do_clear(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.fill("", timeout=timeout_s * 1000)
    return _done(handle, "clear", url_before)


async def do_scroll(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.scroll_into_view_if_needed(timeout=timeout_s * 1000)
    return _done(handle, "scroll", url_before)


async def do_check(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.check(timeout=timeout_s * 1000)
    return _done(handle, "check", url_before)


async def do_uncheck(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.uncheck(timeout=timeout_s * 1000)
    return _done(handle, "uncheck", url_before)


async def do_select(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    value = kw.get("value")
    by = kw.get("select_by", "value")
    if by == "label":
        await loc.select_option(label=value, timeout=timeout_s * 1000)
    elif by == "index":
        await loc.select_option(index=int(value), timeout=timeout_s * 1000)
    else:
        await loc.select_option(value=value, timeout=timeout_s * 1000)
    return _done(handle, "select", url_before)


async def do_upload(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    url_before = handle.page.url
    await loc.set_input_files(kw.get("files"), timeout=timeout_s * 1000)
    return _done(handle, "upload", url_before)


async def do_drag(backend, handle, target, *, timeout_s=30.0, **kw):
    loc = await backend.resolve(handle, target)
    destination = await backend.resolve(handle, kw.get("destination"))
    url_before = handle.page.url
    await loc.drag_to(destination, timeout=timeout_s * 1000)
    return _done(handle, "drag", url_before)


TARGET_HANDLERS = {
    "click": do_click,
    "fill": do_fill,
    "type": do_type,
    "press": do_press,
    "hover": do_hover,
    "focus": do_focus,
    "clear": do_clear,
    "scroll": do_scroll,
    "check": do_check,
    "uncheck": do_uncheck,
    "select": do_select,
    "upload": do_upload,
    "drag": do_drag,
}


def handler(action):
    fn = TARGET_HANDLERS.get(action)
    if fn is None:
        raise GavelError("bad_action", f"未知动作：{action}")
    return fn
