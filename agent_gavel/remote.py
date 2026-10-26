"""adapter 侧：连接或拉起 daemon，并按 daemon 返回的 schema 动态注册工具。

本模块只依赖 mcp 与标准库；不得导入 playwright / runtime / tools，
否则 adapter 会为每个客户端重复加载浏览器栈。
"""

import asyncio
import json
import subprocess
import sys
import time

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .core import config
from .core.errors import GavelError, wrap_exception
from .core.logging import setup as setup_logging

DAEMON_MODULE = "agent_gavel.daemon"


def _socket_path():
    return config.data_dir() / "daemon.sock"


def _spawn_daemon():
    root = config.data_dir()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "daemon.log", "ab", buffering=0) as log:
        subprocess.Popen(
            [sys.executable, "-m", DAEMON_MODULE],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, close_fds=True)


class DaemonClient:
    def __init__(self, path=None):
        self.path = path or _socket_path()
        self._reader = None
        self._writer = None
        self._pending = {}
        self._seq = 0
        self._pump = None
        self._token = None
        self._lock = asyncio.Lock()

    # ---- 连接 ----
    def _info(self):
        return json.loads((config.data_dir() / "daemon.json").read_text())

    async def _attach(self):
        info = self._info()
        self._token = info.get("token")
        if info.get("port"):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", int(info["port"]), limit=64 * 1024 * 1024)
        else:
            reader, writer = await asyncio.open_unix_connection(
                info["socket"], limit=64 * 1024 * 1024)
        self._reader, self._writer = reader, writer
        self._pump = asyncio.create_task(self._read_loop(reader, writer))

    async def _read_loop(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                fut = self._pending.pop(msg.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except Exception:
            pass
        finally:
            if self._writer is writer:
                self._drop()

    def _drop(self):
        self._fail_pending()
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

    def _fail_pending(self):
        err = GavelError("daemon_unavailable", "daemon 连接已断开",
                         retryable=True,
                         hint="重试即可；adapter 会重新拉起或连接 daemon")
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()

    async def ensure(self, timeout=30.0):
        """确保已连接；必要时拉起 daemon。并发调用由锁串行化。"""
        async with self._lock:
            if self._writer is not None:
                return
            try:
                await asyncio.wait_for(self._attach(), timeout=1.0)
                return
            except Exception:
                pass
            _spawn_daemon()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(self._attach(), timeout=1.0)
                    return
                except Exception:
                    await asyncio.sleep(0.1)
            raise GavelError(
                "daemon_unavailable", "daemon 未能在限定时间内启动",
                retryable=True,
                hint=f"查看 {config.data_dir() / 'logs' / 'daemon.log'}")

    async def call(self, tool, args=None):
        if self._writer is None:
            await self.ensure()
        self._seq += 1
        rid = self._seq
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"id": rid, "token": self._token,
                              "tool": tool, "args": args or {}},
                             ensure_ascii=False)
        try:
            self._writer.write((payload + "\n").encode())
            await self._writer.drain()
        except Exception as e:
            self._pending.pop(rid, None)
            self._drop()
            raise GavelError("daemon_unavailable", f"daemon 写入失败：{e}",
                             retryable=True) from e
        msg = await fut
        if "error" in msg:
            return msg["error"], True
        return msg["result"], False

    async def close(self):
        if self._pump is not None:
            self._pump.cancel()
        self._drop()


def _version():
    try:
        from importlib.metadata import version
        return version("agent-gavel")
    except Exception:
        return ""


async def _serve(client, tools):
    parsed = [types.Tool.model_validate(t) for t in tools]

    async def on_list_tools(context, params):
        return types.ListToolsResult(tools=parsed)

    async def on_call_tool(context, params):
        try:
            result, is_error = await client.call(params.name,
                                                 params.arguments or {})
        except GavelError as e:
            result, is_error = e.to_dict(), True
        except Exception as e:  # noqa: BLE001
            result, is_error = wrap_exception(e).to_dict(), True
        return types.CallToolResult(
            content=[types.TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, default=str))],
            structured_content=result if isinstance(result, dict) else None,
            is_error=is_error,
        )

    server = Server("agent-gavel", version=_version(),
                    on_list_tools=on_list_tools,
                    on_call_tool=on_call_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


async def run():
    setup_logging()
    client = DaemonClient()
    await client.ensure()
    try:
        desc, is_error = await client.call("__describe__")
        if is_error:
            raise GavelError("daemon_unavailable", str(desc),
                             hint="查看 daemon.log")
        await _serve(client, desc["tools"])
    finally:
        await client.close()
