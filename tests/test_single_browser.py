"""同一资料目录只允许一个 Chrome：活着就复用，死了才重开，绝不并存。"""

import os
import signal
import time

import pytest

from agent_gavel.runtime.browser_runtime import BrowserRuntime
from agent_gavel.runtime.browser_process import _managed_pids, _state_path


def _kill(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


async def test_reuse_without_state_file(monkeypatch, tmp_path):
    """状态文件丢了也必须能靠 /proc 找回同一个进程，而不是再开一个。"""
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    rt = BrowserRuntime()
    h1 = await rt.ensure_browser()
    pid = h1.pid
    assert len(_managed_pids(str(tmp_path / "profiles" / "primary"))) == 1

    os.remove(_state_path(str(tmp_path / "profiles" / "primary")))
    await rt.shutdown(keep_browsers=True)

    rt2 = BrowserRuntime()
    h2 = await rt2.ensure_browser()
    assert h2.pid == pid, (pid, h2.pid)
    assert len(_managed_pids(str(tmp_path / "profiles" / "primary"))) == 1
    await rt2.shutdown(keep_browsers=False)
    _kill(pid)


async def test_dead_browser_is_replaced(monkeypatch, tmp_path):
    """外部杀掉 Chrome 后，下一次 ensure 只起一个新的，旧进程不残留。"""
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    rt = BrowserRuntime()
    h1 = await rt.ensure_browser()
    pid = h1.pid

    _kill(pid)
    for _ in range(50):
        if not _managed_pids(str(tmp_path / "profiles" / "primary")):
            break
        time.sleep(0.1)

    h2 = await rt.ensure_browser()
    assert h2.pid != pid
    assert len(_managed_pids(str(tmp_path / "profiles" / "primary"))) == 1
    await rt.shutdown(keep_browsers=False)
    _kill(h2.pid)


async def test_leftover_without_registry_is_reused(monkeypatch, tmp_path):
    """daemon 崩溃留下的 Chrome：新 runtime 必须接管它，不能再开一个。"""
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    rt = BrowserRuntime()
    h1 = await rt.ensure_browser()
    pid = h1.pid
    await rt.shutdown(keep_browsers=True)

    rt2 = BrowserRuntime()
    h2 = await rt2.ensure_browser()
    assert h2.pid == pid, (pid, h2.pid)
    assert len(_managed_pids(str(tmp_path / "profiles" / "primary"))) == 1
    await rt2.shutdown(keep_browsers=False)
    _kill(pid)


def test_launch_args_carry_no_automation_flags():
    """启动参数里不能出现可枚举的自动化标志。"""
    from agent_gavel.runtime.browser_process import _launch_args

    args = _launch_args()
    for bad in (
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--keep-alive-for-test",
        "--enable-automation",
    ):
        assert bad not in args, (bad, args)
    assert "--disable-blink-features=AutomationControlled" in args


async def test_closing_last_tab_keeps_browser_alive(gavel_tools):
    """关掉默认容器的最后一个标签页时必须补一个空白页，替代 keep-alive。"""
    T = gavel_tools
    ctx = await T["context_create"]()
    items = await T["page_list"]()
    for item in [x for x in items["items"] if x["context_id"] == ctx["context_id"]]:
        await T["page_close"](page_id=item["page_id"])
    after = await T["page_list"]()
    assert any(x["context_id"] == ctx["context_id"] for x in after["items"]), after
