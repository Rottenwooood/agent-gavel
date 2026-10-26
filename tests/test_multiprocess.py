"""多进程验收：1 个 daemon + 多个 adapter，走真实 MCP stdio。

这不是单元测试：每个 adapter 都是独立的 agent_gavel.main 进程，
工具调用经 Unix socket 转发给 daemon，daemon 再驱动真实浏览器。
"""

import asyncio
import contextlib
import json
import os
import pathlib
import signal
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _env(data: pathlib.Path) -> dict:
    return {
        **os.environ,
        "AGENT_GAVEL_DATA_DIR": str(data),
        "AGENT_GAVEL_BROWSER_PROFILE": str(data / "profile"),
        "AGENT_GAVEL_BROWSER_PROFILES": str(data / "profiles"),
        "AGENT_GAVEL_WORKFLOWS_DIR": str(data / "workflows"),
        "AGENT_GAVEL_HEADLESS": os.environ.get("AGENT_GAVEL_HEADLESS", "0"),
        "DISPLAY": os.environ.get("DISPLAY", ":1"),
    }


def _params(env):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "agent_gavel.main"], env=env, cwd=str(ROOT)
    )


async def _rpc(session, name, args=None):
    res = await session.call_tool(name, args or {})
    sc = getattr(res, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    text = "".join(getattr(c, "text", "") for c in res.content)
    return json.loads(text)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "daemon-data"
    d.mkdir()
    yield d
    info = d / "daemon.json"
    if info.exists():
        try:
            pid = int(json.loads(info.read_text())["pid"])
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        for _ in range(100):
            if not info.exists():
                break
            time.sleep(0.1)


async def test_one_daemon_many_adapters(data_dir, webapp_server):
    env = _env(data_dir)
    async with contextlib.AsyncExitStack() as stack:
        sessions = []
        for _ in range(4):
            read, write = await stack.enter_async_context(stdio_client(_params(env)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions.append(session)

        tool_sets = [await s.list_tools() for s in sessions]
        counts = {len(t.tools) for t in tool_sets}
        assert len(counts) == 1 and counts.pop() > 0
        names = {t.name for t in tool_sets[0].tools}
        assert {"browser_status", "session_create", "workflow_run"} <= names

        statuses = await asyncio.gather(*[_rpc(s, "browser_status") for s in sessions])
        daemon_pids = {st["daemon_pid"] for st in statuses}
        assert len(daemon_pids) == 1, daemon_pids
        assert all(st["adapters"] == 4 for st in statuses), statuses

        created = await _rpc(sessions[0], "session_create")
        assert created["status"] == "ok", created
        page_id, session_id = created["page_id"], created["session_id"]
        nav = await _rpc(
            sessions[0], "page_navigate", {"page_id": page_id, "url": f"{webapp_server}/basic.html"}
        )
        assert nav["status"] == "ok", nav
        after_launch = await _rpc(sessions[0], "browser_status")
        assert len(after_launch["processes"]) == 1, after_launch

        lists = await asyncio.gather(*[_rpc(s, "page_list") for s in sessions])
        id_sets = [{p["page_id"] for p in item["items"]} for item in lists]
        assert all(ids == id_sets[0] for ids in id_sets), id_sets

        fills = await asyncio.gather(
            *[
                _rpc(
                    s,
                    "locator_fill",
                    {
                        "page_id": page_id,
                        "target": {"by": "css", "value": "#text-input"},
                        "value": f"v{i}",
                    },
                )
                for i, s in enumerate(sessions)
            ]
        )
        assert all(r["status"] == "ok" for r in fills), fills

        read = await _rpc(
            sessions[0],
            "page_read",
            {
                "page_id": page_id,
                "page_features": {"v": "document.querySelector('#text-input').value"},
            },
        )
        assert read["features"]["v"] in {f"v{i}" for i in range(4)}, read

        tmpl = {
            "template_id": "mp_busy",
            "steps": [
                {"id": "go", "action": {"type": "navigate", "url": f"{webapp_server}/basic.html"}},
                {
                    "id": "w",
                    "action": {
                        "type": "wait_for_selector",
                        "selector": "#never-appears",
                        "timeout_s": 3,
                    },
                },
            ],
        }
        saved = await _rpc(sessions[0], "workflow_save", {"template": tmpl})
        assert saved["status"] == "ok", saved
        r1, r2 = await asyncio.gather(
            _rpc(sessions[1], "workflow_run", {"name_or_id": "mp_busy", "session_id": session_id}),
            _rpc(sessions[2], "workflow_run", {"name_or_id": "mp_busy", "session_id": session_id}),
        )
        reasons = {r1.get("reason"), r2.get("reason")}
        assert "session_busy" in reasons, (r1, r2)

        await _rpc(sessions[0], "session_close", {"session_id": session_id})

        final = await _rpc(sessions[3], "browser_status")
        assert len(final["processes"]) == 1
        assert len(final["os_browsers"]) == 1


async def test_daemon_crash_recovery(data_dir, webapp_server):
    """daemon 被杀后：adapter 不应崩溃；新 daemon 复用原 Chrome，且不出现两个 daemon。"""
    env = _env(data_dir)
    async with contextlib.AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(_params(env)))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        created = await _rpc(session, "session_create")
        assert created["status"] == "ok", created
        nav = await _rpc(
            session,
            "page_navigate",
            {"page_id": created["page_id"], "url": f"{webapp_server}/basic.html"},
        )
        assert nav["status"] == "ok", nav

        info_path = data_dir / "daemon.json"
        pid1 = int(json.loads(info_path.read_text())["pid"])
        os.kill(pid1, signal.SIGKILL)
        for _ in range(100):
            try:
                os.kill(pid1, 0)
            except OSError:
                break
            await asyncio.sleep(0.05)

        status = None
        for _ in range(120):
            try:
                candidate = await _rpc(session, "browser_status")
            except Exception:
                candidate = {}
            if candidate.get("status") == "ok":
                status = candidate
                break
            await asyncio.sleep(0.25)
        assert status is not None, "daemon 崩溃后 adapter 无法恢复"

        pid2 = int(json.loads(info_path.read_text())["pid"])
        assert pid2 != pid1
        assert len(status["os_browsers"]) == 1, status
        assert status["daemon_pid"] == pid2

        # 新 adapter 必须加入同一个 daemon，而不是再起一个
        read2, write2 = await stack.enter_async_context(stdio_client(_params(env)))
        session2 = await stack.enter_async_context(ClientSession(read2, write2))
        await session2.initialize()
        other = await _rpc(session2, "browser_status")
        assert other["daemon_pid"] == pid2

        # 新 daemon 仍能正常驱动浏览器
        created2 = await _rpc(session, "session_create")
        assert created2["status"] == "ok", created2
        nav2 = await _rpc(
            session,
            "page_navigate",
            {"page_id": created2["page_id"], "url": f"{webapp_server}/basic.html"},
        )
        assert nav2["status"] == "ok", nav2
        final = await _rpc(session, "browser_status")
        assert len(final["os_browsers"]) == 1, final
