"""daemon 空转退出：没有 adapter 连接时不该一直挂着浏览器。"""

import asyncio

from agent_gavel.daemon import _idle_watch


async def test_idle_daemon_exits_when_no_adapter():
    state = {"adapters": 0}
    stop = asyncio.Event()
    task = asyncio.create_task(_idle_watch(state, 0.2, stop))
    await asyncio.wait_for(stop.wait(), timeout=3)
    task.cancel()
    assert stop.is_set()


async def test_daemon_stays_while_adapter_connected():
    state = {"adapters": 1}
    stop = asyncio.Event()
    task = asyncio.create_task(_idle_watch(state, 0.2, stop))
    await asyncio.sleep(0.6)
    assert not stop.is_set()
    state["adapters"] = 0
    await asyncio.wait_for(stop.wait(), timeout=3)
    task.cancel()
    assert stop.is_set()


async def test_idle_exit_zero_disables():
    state = {"adapters": 0}
    stop = asyncio.Event()
    task = asyncio.create_task(_idle_watch(state, 0, stop))
    await asyncio.sleep(0.4)
    assert not stop.is_set()
    task.cancel()


async def test_idle_exit_keeps_browser_alive(monkeypatch, tmp_path):
    """空闲退出只断连接：受管 Chrome 必须活着，下次直接重连。"""
    import os
    import signal

    from agent_gavel.runtime.browser_runtime import BrowserRuntime

    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    rt = BrowserRuntime()
    await rt.ensure_browser()
    pids = [p["pid"] for p in rt.processes.list() if p.get("pid")]
    assert pids, "没有启动受管 Chrome"

    await rt.shutdown(keep_browsers=True)
    await asyncio.sleep(0.5)
    alive = [p for p in pids if os.path.exists(f"/proc/{p}")]
    assert alive == pids, f"空闲退出杀掉了 Chrome：{pids} -> {alive}"

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


async def test_new_daemon_reattaches_running_chrome(monkeypatch, tmp_path):
    """daemon 退出后不杀 Chrome；下一个 runtime 按 state 文件重连同一个进程。"""
    import os
    import signal

    from agent_gavel.runtime.browser_runtime import BrowserRuntime

    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    rt1 = BrowserRuntime()
    h1 = await rt1.ensure_browser()
    pid = h1.pid
    await rt1.shutdown(keep_browsers=True)

    rt2 = BrowserRuntime()
    h2 = await rt2.ensure_browser()
    assert h2.pid == pid, (pid, h2.pid)
    await rt2.shutdown(keep_browsers=True)

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
