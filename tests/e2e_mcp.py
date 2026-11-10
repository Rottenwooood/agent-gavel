"""Black-box acceptance through MCP stdio, daemon and a real browser.

Run: python tests/e2e_mcp.py [--headless] [--list-only] [--output PATH]
No imports from agent_gavel, mocks, registry calls or private browser APIs.
The default is headed managed Chrome with a fresh, test-owned profile.
"""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"browser", "session", "context", "page", "locator", "workflow", "artifact",
            "assertion", "trace", "network", "console_get", "performance_metrics",
            "daemon_shutdown", "screenshot"}


class MCP:
    def __init__(self, env):
        self.env, self.seq, self.pending = env, 0, {}
        self.proc = None
        self.rows = []

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agent_gavel.main", cwd=ROOT, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self._read())
        await self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "gavel-e2e", "version": "1"}})
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await self.proc.stdin.drain()

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                msg = json.loads(line)
                future = self.pending.pop(msg.get("id"), None)
                if future is not None:
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
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                          "method": method, "params": params}) + "\n").encode())
        await self.proc.stdin.drain()
        msg = await asyncio.wait_for(future, timeout=50)
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg["result"]

    async def call(self, name, **args):
        started = time.perf_counter()
        reply = await self.rpc("tools/call", {"name": name, "arguments": args})
        result = reply.get("structuredContent")
        if result is None:
            for block in reply.get("content", []):
                if block["type"] == "text":
                    result = json.loads(block["text"])
                    break
        result = result or {}
        self.rows.append({"tool": name, "op": args.get("op"), "status": result.get("status"),
                          "reason": result.get("reason"), "isError": reply.get("isError", False),
                          "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                          "json_bytes": len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()),
                          "content_types": [b["type"] for b in reply.get("content", [])]})
        return result, reply

    async def ok(self, name, **args):
        result, reply = await self.call(name, **args)
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


async def exercise(client, env, options, report):
    tools = (await client.rpc("tools/list", {}))["tools"]
    assert {t["name"] for t in tools} == EXPECTED, [t["name"] for t in tools]
    tool_bytes = len(json.dumps({"tools": tools}, ensure_ascii=False, separators=(",", ":")).encode())
    assert tool_bytes < 42844, tool_bytes
    report["tools"] = {"count": len(tools), "bytes": tool_bytes}
    invalid, wire = await client.call("locator", op="fill", value="missing target")
    assert invalid["status"] == "error" and wire.get("isError"), invalid
    wrong, wire = await client.call("locator", op="fill", target={"css": "#q"}, files=["wrong"])
    assert wrong["status"] == "error" and wire.get("isError"), wrong
    assert (await client.ok("session", op="list"))["items"] == []
    assert (await client.ok("page", op="list"))["items"] == []
    for name, args in (("browser", {"op": "status"}), ("performance_metrics", {}),
                       ("artifact", {"op": "list"}), ("workflow", {"op": "list"}),
                       ("console_get", {}), ("assertion", {"op": "schema"})):
        await client.ok(name, **args)
    compact = await client.ok("browser", op="status")
    full = await client.ok("browser", op="status", detail="full")
    assert "metrics" not in compact and "metrics" in full
    assert "operation_id" not in compact and "operation_id" in full
    report["protocol_checks"] = "passed"
    if options.list_only:
        return

    config = {"op": "create", "headless": options.headless}
    if options.browser_mode:
        config["browser_mode"] = options.browser_mode
    created = await client.ok("session", **config)
    sid, pid, cid = created["session_id"], created["page_id"], created["context_id"]
    report["browser_created"] = True
    base = (ROOT / "tests" / "webapp").as_uri()
    await client.ok("page", op="navigate", page_id=pid, url=f"{base}/basic.html")
    read = await client.ok("page", op="read", page_id=pid,
                           page_features={"none": "null", "zero": "0", "no": "false", "empty": "[]"})
    assert read["features"] == {"none": None, "zero": 0, "no": False, "empty": []}
    await client.ok("locator", op="fill", page_id=pid, target={"css": "#text-input"}, value="e2e")
    await client.ok("locator", op="click", page_id=pid, target={"css": "#btn"},
                    assertions={"text_eq": {"selector": "#out", "value": "clicked"}})
    await client.ok("assertion", op="check", page_id=pid, checks={"title_contains": "Basic"})
    fail, wire = await client.call("assertion", op="check", page_id=pid,
                                   checks={"title_eq": "not this title"}, wait_s=0.05)
    assert fail["status"] == "fail" and "assertions" in fail
    missing, wire = await client.call("locator", op="click", page_id=pid, target={"css": "#missing"})
    assert missing["reason"] == "locator_not_found" and missing["retryable"] and wire.get("isError")

    for level in ("summary", "compact", "interactive", "full"):
        snapshot = await client.ok("page", op="snapshot", page_id=pid, level=level)
        if level != "summary":
            assert snapshot["elements"]
    for op in ("get", "meta", "text", "links", "explore"):
        await client.ok("page", op=op, page_id=pid)
    await client.ok("page", op="find_text", page_id=pid, text="Click me")
    for op in ("wait_for_load", "wait_for_url", "wait_for_selector", "wait_for_text"):
        args = {"op": op, "page_id": pid, "timeout_s": 2}
        args.update({"wait_for_url": {"url": "**/basic.html"},
                     "wait_for_selector": {"selector": "#btn"},
                     "wait_for_text": {"text": "Basic"}}.get(op, {}))
        await client.ok("page", **args)

    images = []
    for scope in ({}, {"full_page": True}, {"selector": "#btn"}):
        metadata, wire = await client.call("screenshot", page_id=pid, **scope)
        assert not wire.get("isError"), metadata
        blocks = [block for block in wire["content"] if block["type"] == "image"]
        assert len(blocks) == 1 and blocks[0]["mimeType"] == "image/png", wire
        png = base64.b64decode(blocks[0]["data"], validate=True)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", png[16:24])
        assert (metadata["width"], metadata["height"]) == (width, height)
        assert width > 0 and height > 0 and metadata["artifact_id"]
        images.append({"scope": scope, "width": width, "height": height, "bytes": len(png)})
        (Path(env["AGENT_GAVEL_DATA_DIR"]) / f"capture-{len(images)}.png").write_bytes(png)
    report["screenshots"] = images
    aid = metadata["artifact_id"]
    await client.ok("artifact", op="get", artifact_id=aid)
    mismatch, wire = await client.call("artifact", op="get", artifact_id=aid, expected_kind="download")
    assert mismatch["reason"] == "not_a_download" and wire.get("isError")
    await client.ok("artifact", op="export", artifact_id=aid, dest_dir=env["AGENT_GAVEL_DATA_DIR"])

    # Real workflow save/validate/replay and observable DOM outcome.
    template = {"template_id": "e2e_flow", "steps": [
        {"id": "go", "action": {"type": "navigate", "url": f"{base}/basic.html"}},
        {"id": "fill", "action": {"type": "fill", "target": {"css": "#text-input"}, "value": "$QUERY"}},
        {"id": "click", "action": {"type": "click", "target": {"css": "#btn"}},
         "checkpoint": {"type": "text_eq", "selector": "#out", "value": "clicked"}},
    ]}
    await client.ok("workflow", op="validate", template=template)
    await client.ok("workflow", op="save", template=template)
    run = await client.ok("workflow", op="run", name_or_id="e2e_flow", params={"QUERY": "replayed"}, session_id=sid)
    assert run["steps_completed"] == 3
    value = await client.ok("page", op="read", page_id=pid,
                            page_features={"value": "document.querySelector('#text-input').value"})
    assert value["features"]["value"] == "replayed"
    await client.ok("workflow", op="get", name_or_id="e2e_flow")

    # Independent adapter must see the same ids; no second browser is created.
    other = MCP(env)
    await other.start()
    try:
        pages = await other.ok("page", op="list")
        assert pid in {p["page_id"] for p in pages["items"]}
    finally:
        await other.close()
    await client.ok("network", op="start", context_id=cid)
    await client.ok("trace", op="start", context_id=cid)
    await client.ok("page", op="reload", page_id=pid)
    await client.ok("network", op="stop", context_id=cid)
    trace = await client.ok("trace", op="stop", context_id=cid)
    await client.ok("artifact", op="get", artifact_id=trace["artifact_id"])
    await client.ok("context", op="set_headers", context_id=cid, headers={"X-E2E": "1"})
    await client.ok("context", op="set_permissions", context_id=cid, permissions=[])
    context = await client.ok("context", op="create", session_id=sid, container="isolated")
    await client.ok("context", op="close", context_id=context["context_id"])
    await client.ok("workflow", op="delete", template_id="e2e_flow")
    await client.ok("artifact", op="delete", artifact_id=aid)
    await client.ok("session", op="close", session_id=sid)
    report["browser_checks"] = "passed"


async def main(options):
    report = {"mode": "headless" if options.headless else "headed", "status": "failed"}
    with tempfile.TemporaryDirectory(prefix="gavel-mcp-e2e-") as data:
        env = dict(os.environ)
        env.update({"AGENT_GAVEL_DATA_DIR": data, "AGENT_GAVEL_ARTIFACT_DIR": f"{data}/artifacts",
                    "AGENT_GAVEL_WORKFLOWS_DIR": f"{data}/workflows",
                    "AGENT_GAVEL_BROWSER_PROFILE": f"{data}/browser-profile",
                    "AGENT_GAVEL_BROWSER_PROFILES": f"{data}/profiles",
                    "AGENT_GAVEL_HEADLESS": "1" if options.headless else "0",
                    "AGENT_GAVEL_ALLOW_PATHS": data, "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(ROOT)})
        # Prevent inherited settings from attaching to the user's browser.
        env.pop("AGENT_GAVEL_CDP_ENDPOINT", None)
        env["AGENT_GAVEL_BROWSER_MODE"] = options.browser_mode or "managed"
        client = MCP(env)
        try:
            await client.start()
            await exercise(client, env, options, report)
            report["status"] = "passed"
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            if client.proc is not None and client.proc.returncode is None:
                try:
                    await asyncio.wait_for(client.call("daemon_shutdown", confirm=True, force=True), timeout=4)
                except Exception:
                    pass
            await client.close()
            report["calls"] = client.rows
            log = Path(data, "logs", "daemon.log")
            if report["status"] != "passed" and log.exists():
                report["daemon_log_tail"] = log.read_text(errors="replace")[-5000:]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if options.output:
        Path(options.output).parent.mkdir(parents=True, exist_ok=True)
        Path(options.output).write_text(text + "\n")
    print(text)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--browser-mode", choices=("managed", "playwright"))
    parser.add_argument("--output")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
