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
