"""M1 runtime 验收：session/context/page 生命周期 + 并发 + 资源无泄漏。

见 docs/tech-plan.md §9（M1）与 §12（性能门槛）。
"""

import asyncio
import time

import pytest

from agent_gavel.runtime.errors import GavelError
from agent_gavel.tools.browser_tools import register_browser_tools


class FakeMCP:
    """只收集 @mcp.tool() 注册的函数，用于直接调用工具层。"""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


async def _open(rt, url=None):
    session = await rt.session_mgr.create(mode="ephemeral")
    ctx = rt.contexts[session.context_ids[0]]
    handle = await rt.page_mgr.open(ctx, url)
    return session, ctx, handle


EXPECTED_TOOLS = {
    "browser_launch", "browser_status", "browser_close",
    "session_create", "session_list", "session_get", "session_reset",
    "session_close", "session_export_state", "session_import_state",
    "context_create", "context_close",
    "page_open", "page_list", "page_get", "page_focus", "page_close",
    "page_select", "page_wait", "page_reload", "page_go_back", "page_go_forward",
}


async def test_mcp_tools_registered(runtime):
    mcp = FakeMCP()
    register_browser_tools(mcp)
    assert EXPECTED_TOOLS <= set(mcp.tools)
    r = await mcp.tools["browser_status"]()
    assert r["status"] == "ok"
    assert "counts" in r


async def test_launch_and_status(runtime):
    handle = await runtime.ensure_default_browser()
    assert handle.alive
    st = runtime.status()
    assert st["counts"]["processes"] >= 1


async def test_session_page_lifecycle(runtime, webapp_server):
    session, ctx, handle = await _open(runtime, f"{webapp_server}/basic.html")
    assert await handle.page.title() == "Basic Page"
    assert handle.to_dict()["page_status"] == "open"
    await runtime.page_mgr.close(handle.page_id)
    await runtime.session_mgr.close(session.session_id)
    assert len(runtime.pages) == 0
    assert len(runtime.contexts) == 0
    assert len(runtime.sessions) == 0


async def test_session_isolation(runtime, webapp_server):
    _s1, _c1, h1 = await _open(runtime, f"{webapp_server}/basic.html")
    _s2, _c2, h2 = await _open(runtime, f"{webapp_server}/basic.html")
    await h1.page.evaluate("localStorage.setItem('k','v1')")
    await h2.page.evaluate("localStorage.setItem('k','v2')")
    assert await h1.page.evaluate("localStorage.getItem('k')") == "v1"
    assert await h2.page.evaluate("localStorage.getItem('k')") == "v2"


async def test_same_page_serialized(runtime):
    _s, _c, handle = await _open(runtime)
    order = []

    async def call(i):
        async def fn():
            order.append(i)
            await asyncio.sleep(0.001)
            return i
        return await handle.actor.submit(f"op_{i}", fn)

    results = await asyncio.gather(*[call(i) for i in range(10)])
    assert order == list(range(10))
    assert results == list(range(10))


async def test_cross_page_parallel(runtime):
    _s, ctx, h1 = await _open(runtime)
    h2 = await runtime.page_mgr.open(ctx)

    async def slow(handle):
        async def fn():
            await asyncio.sleep(0.3)
            return True
        return await handle.actor.submit("slow", fn)

    t0 = time.perf_counter()
    await asyncio.gather(slow(h1), slow(h2))
    assert time.perf_counter() - t0 < 0.55


async def test_timeout(runtime):
    _s, _c, handle = await _open(runtime)

    async def fn():
        await asyncio.sleep(1)

    with pytest.raises(GavelError) as ei:
        await handle.actor.submit("slow", fn, timeout_s=0.1)
    assert ei.value.code == "timeout"


async def test_cancel(runtime):
    _s, _c, handle = await _open(runtime)

    async def fn():
        await asyncio.sleep(0.5)
        return 1

    task = asyncio.ensure_future(
        handle.actor.submit("a", fn, operation_id="opX"))
    await asyncio.sleep(0.05)
    assert handle.actor.cancel("opX") is True
    with pytest.raises(GavelError) as ei:
        await task
    assert ei.value.code == "cancelled"


async def test_page_close_cancels_pending(runtime):
    _s, _c, handle = await _open(runtime)

    async def fn():
        await asyncio.sleep(5)

    task = asyncio.ensure_future(handle.actor.submit("long", fn))
    await asyncio.sleep(0.05)
    await runtime.page_mgr.close(handle.page_id)
    with pytest.raises(GavelError) as ei:
        await task
    assert ei.value.code in ("page_closed", "cancelled")


async def test_session_busy_exclusive(runtime):
    session = await runtime.session_mgr.create(mode="ephemeral")
    await session.acquire("run_a")
    with pytest.raises(GavelError) as ei:
        await session.acquire("run_b")
    assert ei.value.code == "session_busy"
    session.release("run_a")
    await session.acquire("run_b")  # 释放后可再占
    assert session.busy_owner == "run_b"


async def test_no_leak_1000_ops(runtime):
    _s, _c, handle = await _open(runtime)
    counter = {"n": 0}

    async def fn():
        counter["n"] += 1

    await asyncio.gather(*[handle.actor.submit(f"op_{i}", fn)
                           for i in range(1000)])
    assert counter["n"] == 1000
    assert handle.actor.pending == 0
    # _open 建了初始页 + 显式 open 的页，共 2；跑 1000 次不新增
    assert len(runtime.pages) == 2


async def test_ephemeral_sessions_share_default_browser(runtime, gavel_tools):
    """ephemeral 共享默认浏览器进程，但 context 相互隔离。"""
    a = await gavel_tools["session_create"]()
    b = await gavel_tools["session_create"]()
    assert a["process_id"] == b["process_id"]
    assert a["context_id"] != b["context_id"]
    assert a["page_id"] != b["page_id"]


async def test_cold_start_threshold(runtime):
    """方案 §12：冷启动 < 1.5s。"""
    t0 = time.perf_counter()
    await runtime.ensure_default_browser()
    assert time.perf_counter() - t0 < 1.5


async def test_hot_call_threshold(runtime):
    """方案 §12：热连接简单调用 < 20ms。"""
    _s, _c, handle = await _open(runtime)
    await handle.actor.submit("warm", lambda: handle.page.title())
    t0 = time.perf_counter()
    await handle.actor.submit("hot", lambda: handle.page.title())
    assert time.perf_counter() - t0 < 0.020


async def test_crash_recovery_threshold(runtime):
    """方案 §12：浏览器崩溃恢复 < 5s。"""
    handle = await runtime.ensure_default_browser()
    await runtime.processes.close(handle)
    runtime._default_handle = None
    t0 = time.perf_counter()
    h2 = await runtime.ensure_default_browser()
    assert h2.alive
    assert time.perf_counter() - t0 < 5.0


async def test_mcp_page_open_url_and_list(runtime, webapp_server):
    mcp = FakeMCP()
    register_browser_tools(mcp)
    r = await mcp.tools["session_create"]()
    r2 = await mcp.tools["page_open"](
        context_id=r["context_id"], url=f"{webapp_server}/basic.html")
    assert r2["status"] == "ok"
    assert "basic.html" in (r2["url"] or "")
    listing = await mcp.tools["page_list"]()
    assert any(p["page_id"] == r2["page_id"] for p in listing["items"])
