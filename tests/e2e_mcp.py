"""Black-box acceptance through MCP stdio, daemon and a real browser.

Run: python tests/e2e_mcp.py [--headless] [--list-only] [--output PATH]
No imports from agent_gavel, mocks, registry calls or private browser APIs.
The default is headed managed Chrome with a fresh, test-owned profile.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from mcp_client import MCP, cleanup_owned, transport_environment
from mcp_scenarios import capture_images, interactions, profile_copy, workflow_controls

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "browser",
    "session",
    "context",
    "page",
    "locator",
    "workflow",
    "artifact",
    "assertion",
    "trace",
    "network",
    "console_get",
    "performance_metrics",
    "daemon_shutdown",
    "screenshot",
}


async def exercise(client, env, options, report):
    report["stage"] = "tools/list"
    tools = (await client.rpc("tools/list", {}))["tools"]
    assert {t["name"] for t in tools} == EXPECTED, [t["name"] for t in tools]
    tool_bytes = len(
        json.dumps({"tools": tools}, ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert tool_bytes < 42844, tool_bytes
    report["tools"] = {"count": len(tools), "bytes": tool_bytes}
    invalid, wire = await client.call("locator", op="fill", value="missing target")
    assert invalid["status"] == "error" and wire.get("isError"), invalid
    wrong, wire = await client.call("locator", op="fill", target={"css": "#q"}, files=["wrong"])
    assert wrong["status"] == "error" and wire.get("isError"), wrong
    assert (await client.ok("session", op="list"))["items"] == []
    assert (await client.ok("page", op="list"))["items"] == []
    for name, args in (
        ("browser", {"op": "status"}),
        ("performance_metrics", {}),
        ("artifact", {"op": "list"}),
        ("workflow", {"op": "list"}),
        ("console_get", {}),
        ("assertion", {"op": "schema"}),
    ):
        await client.ok(name, **args)
    compact = await client.ok("browser", op="status")
    full = await client.ok("browser", op="status", detail="full")
    assert "metrics" not in compact and "metrics" in full
    assert "operation_id" not in compact and "operation_id" in full
    report["protocol_checks"] = "passed"
    if options.list_only:
        return

    report["stage"] = "browser_launch"
    await client.ok("browser", op="launch", headless=options.headless)
    report["browser_started"] = True
    config = {"op": "create", "headless": options.headless}
    created = await client.ok("session", **config)
    sid, pid, cid = created["session_id"], created["page_id"], created["context_id"]
    report["browser_created"] = True
    base = (ROOT / "tests" / "webapp").as_uri()
    await client.ok("page", op="navigate", page_id=pid, url=f"{base}/basic.html")
    read = await client.ok(
        "page",
        op="read",
        page_id=pid,
        page_features={"none": "null", "zero": "0", "no": "false", "empty": "[]"},
    )
    assert read["features"] == {"none": None, "zero": 0, "no": False, "empty": []}
    assert read["features"]["none"] is None and read["features"]["no"] is False
    assert type(read["features"]["zero"]) is int and isinstance(read["features"]["empty"], list)
    await client.ok("locator", op="fill", page_id=pid, target={"css": "#text-input"}, value="e2e")
    await client.ok(
        "locator",
        op="click",
        page_id=pid,
        target={"css": "#btn"},
        assertions={"text_eq": {"selector": "#out", "value": "clicked"}},
    )
    await client.ok("assertion", op="check", page_id=pid, checks={"title_contains": "Basic"})
    fail, wire = await client.call(
        "assertion", op="check", page_id=pid, checks={"title_eq": "not this title"}, wait_s=0.05
    )
    assert fail["status"] == "fail" and "assertions" in fail
    missing, wire = await client.call(
        "locator", op="click", page_id=pid, target={"css": "#missing"}
    )
    assert missing["reason"] == "locator_not_found" and missing["retryable"] and wire.get("isError")

    for level in ("summary", "compact", "interactive", "full"):
        snapshot = await client.ok("page", op="snapshot", page_id=pid, level=level)
        if level != "summary":
            assert snapshot["elements"]
    for op in ("get", "meta", "text", "explore"):
        await client.ok("page", op=op, page_id=pid)
    await client.ok("page", op="text", mode="links", page_id=pid)
    await client.ok("page", op="find_text", page_id=pid, text="Go")
    for op in ("wait_for_load", "wait_for_url", "wait_for_selector", "wait_for_text"):
        args = {"op": op, "page_id": pid, "timeout_s": 2}
        args.update(
            {
                "wait_for_url": {"url": "**/basic.html"},
                "wait_for_selector": {"selector": "#btn"},
                "wait_for_text": {"text": "Hello"},
            }.get(op, {})
        )
        await client.ok("page", **args)

    aid = await capture_images(client, pid, env, options, report)
    await client.ok("artifact", op="get", artifact_id=aid)
    mismatch, wire = await client.call(
        "artifact", op="get", artifact_id=aid, expected_kind="download"
    )
    assert mismatch["reason"] == "not_a_download" and wire.get("isError")
    await client.ok("artifact", op="export", artifact_id=aid, dest_dir=env["AGENT_GAVEL_DATA_DIR"])

    pdf = await client.ok("page", op="pdf", page_id=pid)
    await client.ok("artifact", op="get", artifact_id=pdf["artifact_id"])
    await interactions(client, pid, cid, sid, base, env["AGENT_GAVEL_DATA_DIR"])
    report["interaction_checks"] = "passed"

    # Real workflow save/validate/replay and observable DOM outcome.
    template = {
        "template_id": "e2e_flow",
        "steps": [
            {"id": "go", "action": {"type": "navigate", "url": f"{base}/basic.html"}},
            {
                "id": "fill",
                "action": {"type": "fill", "target": {"css": "#text-input"}, "value": "$QUERY"},
            },
            {
                "id": "click",
                "action": {"type": "click", "target": {"css": "#btn"}},
                "checkpoint": {"type": "text_eq", "selector": "#out", "value": "clicked"},
            },
        ],
    }
    await client.ok("workflow", op="validate", template=template)
    await client.ok("workflow", op="save", template=template)
    run = await client.ok(
        "workflow", op="run", name_or_id="e2e_flow", params={"QUERY": "replayed"}, session_id=sid
    )
    assert run["steps_completed"] == 3
    value = await client.ok(
        "page",
        op="read",
        page_id=pid,
        page_features={"value": "document.querySelector('#text-input').value"},
    )
    assert value["features"]["value"] == "replayed"
    await client.ok("workflow", op="get", name_or_id="e2e_flow")
    await workflow_controls(client, sid, pid, base)
    await client.ok(
        "workflow", op="publish", template_id="e2e_flow", out_dir=env["AGENT_GAVEL_DATA_DIR"]
    )

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
    leases = await client.ok("session", op="get", session_id=sid)
    assert context["context_id"] not in leases["context_ids"], leases
    await client.ok("workflow", op="delete", template_id="e2e_flow")
    await client.ok("artifact", op="delete", artifact_id=aid)
    await client.ok("session", op="get", session_id=sid)
    reset = await client.ok("session", op="reset", session_id=sid)
    await client.ok("session", op="close", session_id=reset["session_id"])
    await client.ok("artifact", op="cleanup", max_bytes=0)
    if options.browser_mode != "playwright":
        await profile_copy(client, env["AGENT_GAVEL_DATA_DIR"])
        report["profile_checks"] = "passed"
    report["browser_checks"] = "passed"


async def main(options):
    report = {
        "mode": "headless" if options.headless else "headed",
        "status": "failed",
        "scope": "protocol" if options.list_only else "browser",
        "browser_checks": "not_run",
    }
    with tempfile.TemporaryDirectory(prefix="gavel-mcp-e2e-") as data:
        report["environment"] = transport_environment(data)
        env = dict(os.environ)
        env.update(
            {
                "AGENT_GAVEL_DATA_DIR": data,
                "AGENT_GAVEL_ARTIFACT_DIR": f"{data}/artifacts",
                "AGENT_GAVEL_WORKFLOWS_DIR": f"{data}/workflows",
                "AGENT_GAVEL_BROWSER_PROFILE": f"{data}/browser-profile",
                "AGENT_GAVEL_BROWSER_PROFILES": f"{data}/browser-profiles",
                "AGENT_GAVEL_PROFILE_DIR": f"{data}/session-profiles",
                "AGENT_GAVEL_ALLOW_DOMAINS": "",
                "AGENT_GAVEL_DOMAIN_GUARD": "0",
                "AGENT_GAVEL_EXTRA_ARGS": "",
                "AGENT_GAVEL_HEADLESS": "1" if options.headless else "0",
                "AGENT_GAVEL_ALLOW_PATHS": data,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT),
            }
        )
        # Prevent inherited settings from attaching to the user's browser.
        env.pop("AGENT_GAVEL_CDP_ENDPOINT", None)
        env["AGENT_GAVEL_BROWSER_MODE"] = options.browser_mode or "managed"
        client = MCP(env)
        try:
            required = "tcp_loopback" if os.name == "nt" else "unix_socket"
            browser_tcp = not options.list_only and options.browser_mode != "playwright"
            if report["environment"][required] != "available" or (
                browser_tcp and report["environment"]["tcp_loopback"] != "available"
            ):
                report.update(
                    status="blocked",
                    stage="transport_environment",
                    error="Required local socket binding is denied; browser acceptance did not run",
                )
                return report
            report["stage"] = "mcp_initialize"
            await client.start()
            await exercise(client, env, options, report)
            report["status"] = "passed"
            report["stage"] = "complete"
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            if client.proc is not None and client.proc.returncode is None:
                try:
                    await asyncio.wait_for(
                        client.call("daemon_shutdown", confirm=True, force=True), timeout=4
                    )
                except Exception:
                    pass
            await client.close()
            if client.proc is not None:
                await asyncio.to_thread(cleanup_owned, data)
            report["calls"] = client.rows
            if client.proc is not None and client.proc.stderr is not None:
                report["adapter_stderr"] = (await client.proc.stderr.read()).decode(
                    errors="replace"
                )[-5000:]
            log = Path(data, "logs", "daemon.log")
            if (
                client.proc is not None
                and report["status"] != "passed"
                and await asyncio.to_thread(log.exists)
            ):
                report["daemon_log_tail"] = (
                    await asyncio.to_thread(log.read_text, errors="replace")
                )[-5000:]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--browser-mode", choices=("managed", "playwright"))
    parser.add_argument("--output")
    options = parser.parse_args()
    result = asyncio.run(main(options))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if options.output:
        output = Path(options.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n")
    print(text, flush=True)
    raise SystemExit(0 if result["status"] == "passed" else 1)
