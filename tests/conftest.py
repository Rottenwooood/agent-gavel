"""pytest fixtures：本地综合测试站点 + 每个用例独立的 runtime。

测试默认无头（AGENT_GAVEL_HEADLESS），CI 友好；本地调试可自行 export 覆盖。
"""

import functools
import http.server
import os
import threading

import pytest
import pytest_asyncio

# 测试默认无头；显式设置用 setdefault，不覆盖本地调试的 export
os.environ.setdefault("AGENT_GAVEL_HEADLESS", "1")

from agent_gavel.runtime import reset_runtime, shutdown_runtime  # noqa: E402

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")


@pytest.fixture(scope="session")
def webapp_server():
    """线程内起 http.server 服务 tests/webapp/，返回 base_url。"""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=WEBAPP_DIR)
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@pytest_asyncio.fixture
async def runtime():
    rt = reset_runtime()
    try:
        yield rt
    finally:
        await shutdown_runtime()


class FakeMCP:
    """收集 @mcp.tool() 注册的函数，供直接调用工具层。"""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def gavel_tools(runtime):
    """注册全部 runtime 工具，返回 {name: callable}。"""
    from agent_gavel.tools import register_runtime_tools
    m = FakeMCP()
    register_runtime_tools(m)
    return m.tools


@pytest_asyncio.fixture
async def page(gavel_tools, webapp_server):
    """建 session + 打开 basic.html，返回 (tools, page_id, base_url)。"""
    r = await gavel_tools["session_create"]()
    pid = r["page_id"]
    await gavel_tools["page_navigate"](page_id=pid,
                                       url=f"{webapp_server}/basic.html")
    return gavel_tools, pid, webapp_server
