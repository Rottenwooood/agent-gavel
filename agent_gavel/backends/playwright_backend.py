"""Playwright 执行后端（docs/tech-plan.md §5.13）。

把 runtime 的语义操作翻译成 Playwright 调用；tools 层不直接碰 Playwright。
M1 只含生命周期/导航类操作；动作、locator、断言在 M2 补全。
"""


class PlaywrightBackend:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- 导航 / 等待（返回纯数据，工具层包响应）----
    async def goto(self, handle, url, *, wait_until="domcontentloaded",
                   timeout_s=30.0):
        return await handle.page.goto(url, wait_until=wait_until,
                                      timeout=timeout_s * 1000)

    async def wait_load_state(self, handle, state="load", timeout_s=30.0):
        await handle.page.wait_for_load_state(state, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "state": state, "url": handle.page.url}

    async def reload(self, handle, *, wait_until="domcontentloaded",
                     timeout_s=30.0):
        await handle.page.reload(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_back(self, handle, *, wait_until="domcontentloaded",
                      timeout_s=30.0):
        await handle.page.go_back(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_forward(self, handle, *, wait_until="domcontentloaded",
                         timeout_s=30.0):
        await handle.page.go_forward(wait_until=wait_until,
                                     timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}
