"""Black-box MCP stdio client and cleanup of test-owned processes."""

import asyncio
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def transport_environment(data):
    result = {"display": os.environ.get("DISPLAY")}
    for label, family in (
        ("tcp_loopback", socket.AF_INET),
        ("unix_socket", getattr(socket, "AF_UNIX", None)),
    ):
        if family is None:
            result[label] = "unsupported"
            continue
        listener = None
        path = Path(data, "probe.sock")
        try:
            listener = socket.socket(family)
            listener.bind(("127.0.0.1", 0) if family == socket.AF_INET else str(path))
            result[label] = "available"
        except OSError as exc:
            result[label] = str(exc)
        finally:
            if listener:
                listener.close()
            if family != socket.AF_INET and path.exists():
                path.unlink()
    return result


def cleanup_owned(data):
    """Only terminate processes carrying this run's exact private data root."""
    if not Path("/proc").exists():
        return
    expected = ("AGENT_GAVEL_DATA_DIR=" + data).encode()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            command = (process / "cmdline").read_bytes()
            if b"agent_gavel.daemon" not in command and b"chrome" not in command:
                continue
            if expected not in (process / "environ").read_bytes().split(b"\0"):
                continue
            os.kill(int(process.name), signal.SIGKILL)
        except (OSError, PermissionError):
            continue


class MCP:
    def __init__(self, env):
        self.env, self.seq, self.pending = env, 0, {}
        self.proc = None
        self.rows = []

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_gavel.main",
            cwd=ROOT,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        await self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "gavel-e2e", "version": "1"},
            },
        )
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await self.proc.stdin.drain()

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                msg = json.loads(line)
                future = self.pending.pop(msg.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(msg)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("MCP adapter exited"))

    async def rpc(self, method, params):
        self.seq += 1
        request_id = self.seq
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.proc.stdin.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                + "\n"
            ).encode()
        )
        await self.proc.stdin.drain()
        try:
            msg = await asyncio.wait_for(future, timeout=50)
        finally:
            self.pending.pop(request_id, None)
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg["result"]

    async def call(self, tool_name, **args):
        started = time.perf_counter()
        reply = await self.rpc("tools/call", {"name": tool_name, "arguments": args})
        result = reply.get("structuredContent")
        if result is None:
            for block in reply.get("content", []):
                if block["type"] == "text":
                    result = json.loads(block["text"])
                    break
        result = result or {}
        self.rows.append(
            {
                "tool": tool_name,
                "op": args.get("op"),
                "status": result.get("status"),
                "reason": result.get("reason"),
                "isError": reply.get("isError", False),
                "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                "json_bytes": len(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
                ),
                "content_types": [b["type"] for b in reply.get("content", [])],
            }
        )
        return result, reply

    async def ok(self, tool_name, **args):
        result, reply = await self.call(tool_name, **args)
        assert not reply.get("isError"), result
        assert result.get("status") in {"ok", "pass"}, result
        return result

    async def close(self):
        if self.proc is None:
            return
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except TimeoutError:
            self.proc.terminate()
            await self.proc.wait()
        self.reader.cancel()
