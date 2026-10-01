"""进阶/边界覆盖：多 context 隔离、重定向、导航超时、多 session 并行。

（需 M3 的下载/上传/popup/iframe/dialog/storage state 用例在其里程碑补齐。）
"""

import asyncio
import time

import pytest

from agent_gavel.runtime.errors import GavelError
from agent_gavel.tools.common import backend


async def _open(rt, url=None):
    session = await rt.session_mgr.create(mode="ephemeral")
    ctx = rt.contexts[session.context_ids[0]]
    handle = await rt.page_mgr.open(ctx, url)
    return session, ctx, handle


async def test_multi_context_cookie_isolation(runtime, webapp_server):
    session = await runtime.session_mgr.create()
    c1 = runtime.contexts[session.context_ids[0]]
    c2 = await runtime.context_mgr.create(session, {})
    await c1.browser_context.add_cookies(
        [{"name": "k", "value": "v1", "url": webapp_server}])
    ck1 = await c1.browser_context.cookies(webapp_server)
    ck2 = await c2.browser_context.cookies(webapp_server)
    assert any(c["name"] == "k" and c["value"] == "v1" for c in ck1)
    assert not any(c["name"] == "k" for c in ck2)  # 另一个 context 看不到


async def test_server_redirect_followed(runtime, webapp_server):
    """302 服务端重定向：goto 返回时已在最终页。"""
    _s, _c, h = await _open(runtime)
    be = backend()
    r = await h.actor.submit(
        "nav", lambda: be.goto(h, f"{webapp_server}/redirect302"))
    assert r["final_url"].endswith("basic.html")
    assert r["redirected"] is True


async def test_meta_refresh_waited(runtime, webapp_server):
    """meta refresh 是客户端跳转，需 page_wait_for_url 等它发生。"""
    _s, _c, h = await _open(runtime)
    be = backend()
    await h.actor.submit("nav", lambda: be.goto(h, f"{webapp_server}/redirect.html"))
    await h.actor.submit(
        "wait_url",
        lambda: be.wait_for_url(h, "**/basic.html", timeout_s=5))
    assert h.page.url.endswith("basic.html")


async def test_navigation_timeout(runtime, webapp_server):
    _s, _c, h = await _open(runtime)
    be = backend()
    with pytest.raises(GavelError) as ei:
        await h.actor.submit(
            "nav",
            lambda: be.goto(h, f"{webapp_server}/slow", timeout_s=0.5),
            timeout_s=5)
    assert ei.value.code == "navigation_failed"


async def test_multi_session_parallel_work(runtime, webapp_server):
    async def work():
        session = await runtime.session_mgr.create(mode="ephemeral")
        ctx = runtime.contexts[session.context_ids[0]]
        pg = await runtime.page_mgr.open(ctx, f"{webapp_server}/basic.html")
        title = await pg.page.title()
        await runtime.session_mgr.close(session.session_id)
        return title

    t0 = time.perf_counter()
    titles = await asyncio.gather(work(), work(), work())
    dt = time.perf_counter() - t0
    assert titles == ["Basic Page"] * 3
    assert dt < 5.0
    assert len(runtime.sessions) == 0  # 都已清理


async def test_browser_crash_recovery(runtime):
    handle = await runtime.ensure_default_browser()
    await runtime.processes.close(handle)
    runtime._default_handle = None
    h2 = await runtime.ensure_default_browser()
    assert h2.alive
    # 恢复后还能建会话/开页面
    session = await runtime.session_mgr.create()
    assert session.context_ids
