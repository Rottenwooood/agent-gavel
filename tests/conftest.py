"""pytest fixtures：本地综合测试站点 + 每个用例独立的 runtime。

测试默认无头（AGENT_GAVEL_HEADLESS），CI 友好；本地调试可自行 export 覆盖。
"""

import os
import tempfile

import pytest
import pytest_asyncio

# 测试默认无头；显式设置用 setdefault，不覆盖本地调试的 export
os.environ.setdefault("AGENT_GAVEL_HEADLESS", "1")

# 测试用独立的运行数据根，避免写进用户 ~/.config/agent-gavel
os.environ.setdefault("AGENT_GAVEL_DATA_DIR", tempfile.mkdtemp(prefix="agent-gavel-test-data-"))

# 隔离工作流模板目录，避免测试写进真实 ~/.config/agent-gavel/workflows/。
# 注意：不要改 XDG_CONFIG_HOME——Chrome 会读它，指向空目录会导致后续外网导航
# net::ERR_NETWORK_CHANGED/超时（已实测）。
os.environ["AGENT_GAVEL_WORKFLOWS_DIR"] = tempfile.mkdtemp(prefix="agent-gavel-test-workflows-")

# 测试用独立的浏览器持久 profile，避免污染用户 ~/.config/agent-gavel/browser-profile
os.environ.setdefault(
    "AGENT_GAVEL_BROWSER_PROFILE", tempfile.mkdtemp(prefix="agent-gavel-test-profile-")
)
# 资料副本目录也隔离到临时目录
os.environ.setdefault(
    "AGENT_GAVEL_BROWSER_PROFILES", tempfile.mkdtemp(prefix="agent-gavel-test-profiles-")
)

from webapp_server import start_server  # noqa: E402

from agent_gavel.runtime import reset_runtime  # noqa: E402


@pytest.fixture(scope="session")
def webapp_server():
    """线程内起 http.server 服务 tests/webapp/，返回 base_url。"""
    httpd, base = start_server()
    try:
        yield base
    finally:
        httpd.shutdown()


@pytest_asyncio.fixture
async def runtime():
    # 复用同一个浏览器进程，只清 session/context/page；避免每个用例冷启动 Chrome。
    rt = await reset_runtime(keep_browser=True)

    yield rt


def _kill_test_chrome():
    """杀掉用测试资料目录启动的 Chrome（含未登记的残留）。"""
    import signal

    profile = os.environ.get("AGENT_GAVEL_BROWSER_PROFILE")
    if not profile or not os.path.isdir("/proc"):
        return
    markers = (f"--user-data-dir={profile}", "--user-data-dir=/tmp/agent-gavel-test-")
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", "ignore")
        except Exception:
            continue
        if any(marker in cmd for marker in markers) and "chrome" in cmd.lower():
            try:
                os.kill(int(name), signal.SIGKILL)
            except Exception:
                pass


import atexit  # noqa: E402

atexit.register(_kill_test_chrome)


def pytest_sessionfinish(session, exitstatus):
    from agent_gavel.runtime import browser_runtime

    rt = browser_runtime._RUNTIME
    if rt is not None:
        rt.processes.kill_all_sync()
    _kill_test_chrome()


class ToolFixture(dict):
    """Legacy test notation only; the production catalog exposes canonical tools.

    Old behavioral tests can keep their calls while the new black-box acceptance
    exercises the actual MCP tool names. This does not register legacy MCP tools.
    """

    def __init__(self, catalog):
        self.catalog = catalog
        for name in catalog.groups:
            self[name] = self._call(name)

    def _call(self, tool, op=None, defaults=None):
        async def call(**arguments):
            payload = {"detail": "full", **(defaults or {}), **arguments}
            if op is not None:
                payload["op"] = op
            return (await self.catalog.call(tool, payload)).result

        return call

    def __missing__(self, old_name):
        from agent_gavel.tools.migration import mappings

        aliases = mappings(self.catalog)
        if old_name in aliases:
            return self._call(*aliases[old_name])
        tool, op = old_name.split("_", 1)
        if (tool, op) not in self.catalog.operations:
            raise KeyError(old_name)
        return self._call(tool, op)


@pytest.fixture
def gavel_tools(runtime):
    from agent_gavel.tools import create_catalog

    return ToolFixture(create_catalog())


@pytest_asyncio.fixture
async def page(gavel_tools, webapp_server):
    """建 session + 打开 basic.html，返回 (tools, page_id, base_url)。"""
    r = await gavel_tools["session_create"]()
    pid = r["page_id"]
    await gavel_tools["page_navigate"](page_id=pid, url=f"{webapp_server}/basic.html")
    return gavel_tools, pid, webapp_server
