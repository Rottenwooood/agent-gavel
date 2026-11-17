"""Browser scenarios using only the public MCP client interface."""

import asyncio
import base64
import struct
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent / "webapp"


async def interactions(client, pid, cid, sid, base, data):
    await client.ok("page", op="navigate", page_id=pid, url=f"{base}/acceptance.html")
    entry = {"css": "#entry"}
    await client.ok("locator", op="fill", page_id=pid, target=entry, value="ab")
    await client.ok("locator", op="press", page_id=pid, target=entry, keys="End")
    await client.ok("locator", op="type", page_id=pid, target=entry, text="cd")
    value = await client.ok(
        "page",
        op="read",
        page_id=pid,
        page_features={"value": "document.querySelector('#entry').value"},
    )
    assert value["features"]["value"] == "abcd", value
    for op in ("focus", "hover", "clear"):
        await client.ok("locator", op=op, page_id=pid, target=entry)
    await client.ok("locator", op="check", page_id=pid, target={"css": "#check"})
    await client.ok("locator", op="uncheck", page_id=pid, target={"css": "#check"})
    await client.ok("locator", op="select", page_id=pid, target={"css": "#choice"}, value="b")
    upload = Path(data, "upload.txt")
    await asyncio.to_thread(upload.write_text, "e2e upload")
    await client.ok(
        "locator", op="upload", page_id=pid, target={"css": "#upload"}, files=[str(upload)]
    )
    await client.ok(
        "locator",
        op="drag",
        page_id=pid,
        target={"css": "#source"},
        destination={"css": "#destination"},
    )
    await client.ok("locator", op="scroll", page_id=pid, direction="down", amount=1)
    await client.ok("locator", op="scroll", page_id=pid, target=entry)
    state = await client.ok(
        "page",
        op="read",
        page_id=pid,
        page_features={
            "entry": "document.querySelector('#entry').value",
            "checked": "document.querySelector('#check').checked",
            "selected": "document.querySelector('#choice').value",
            "files": "document.querySelector('#upload').files.length",
            "dropped": "document.querySelector('#destination').textContent",
        },
    )
    assert state["features"] == {
        "entry": "",
        "checked": False,
        "selected": "b",
        "files": 1,
        "dropped": "dragged",
    }, state
    await client.ok("locator", op="select", page_id=pid, target={"css": "#choice"}, value=None)
    cleared = await client.ok(
        "page",
        op="read",
        page_id=pid,
        page_features={"index": "document.querySelector('#choice').selectedIndex"},
    )
    assert cleared["features"]["index"] == -1
    await client.ok("locator", op="click", page_id=pid, target={"css": "#delayed"})
    await client.ok("page", op="wait_for_text", page_id=pid, text="Ready", timeout_s=3)
    await client.ok("page", op="handle_dialog", page_id=pid, policy="auto_accept", prompt_text="")
    await client.ok("locator", op="click", page_id=pid, target={"css": "#dialog"})
    await client.ok("page", op="handle_dialog", page_id=pid, policy="manual")

    popup, _ = await asyncio.gather(
        client.ok("page", op="wait_for_popup", page_id=pid, timeout_s=5),
        client.ok("locator", op="click", page_id=pid, target={"css": "#popup"}),
    )
    assert popup["new_page_id"] != pid
    await client.ok("page", op="close", page_id=popup["new_page_id"])
    event = await client.ok(
        "page", op="wait_for_event", kinds=["page_created"], include_past=True, timeout_s=1
    )
    assert event["event"]["event"] == "page_created" and event["event"]["ts"] > 0, event

    download, _ = await asyncio.gather(
        client.ok("page", op="wait_for_download", page_id=pid, timeout_s=5),
        client.ok("locator", op="click", page_id=pid, target={"css": "#download"}),
    )
    aid = download["artifact_id"]
    filtered = await client.ok("artifact", op="list", kind="download", page_id=pid, context_id=sid)
    assert aid in {item["artifact_id"] for item in filtered["items"]}
    await client.ok("artifact", op="get", artifact_id=aid, expected_kind="download")
    text = await client.ok("artifact", op="read_text", artifact_id=aid)
    assert text["extracted"] and text["text"] == "e2e download", text
    exported = await client.ok("artifact", op="export", artifact_id=aid, dest_dir=data)
    assert exported["filename"] == "report.txt" and "exported_to" not in exported
    visible = await client.ok(
        "artifact", op="export", artifact_id=aid, dest_dir=data, path_visibility="user_visible"
    )
    assert visible["filename"] != exported["filename"] and "exported_to" in visible
    await client.ok("artifact", op="delete", artifact_id=aid)

    await client.ok(
        "context",
        op="cookies_set",
        context_id=cid,
        cookies=[{"name": "e2e", "value": "synthetic", "url": "https://e2e.invalid/"}],
    )
    cookies = await client.ok(
        "context", op="cookies_get", context_id=cid, url="https://e2e.invalid/"
    )
    assert any(cookie["name"] == "e2e" for cookie in cookies["cookies"])
    state_path = str(Path(data, "state.json"))
    await client.ok("context", op="export_state", context_id=sid, path=state_path)
    clone = await client.ok("context", op="import_state", path=state_path)
    await client.ok("context", op="close", context_id=clone["context_id"])
    await client.ok("context", op="cookies_clear", context_id=cid)

    await client.ok("page", op="navigate", page_id=pid, url=f"{base}/basic.html")
    await client.ok("page", op="navigate", page_id=pid, url=f"{base}/acceptance.html")
    await client.ok("page", op="go_back", page_id=pid)
    await client.ok("page", op="go_forward", page_id=pid)


async def workflow_controls(client, sid, pid, base):
    slow = {
        "template_id": "e2e_slow",
        "steps": [
            {"id": "go", "action": {"type": "navigate", "url": f"{base}/basic.html"}},
            {
                "id": "wait",
                "action": {"type": "wait_for_selector", "selector": "#never", "timeout_s": 1},
            },
        ],
    }
    await client.ok("workflow", op="save", template=slow)
    one, two = await asyncio.gather(
        client.call(
            "workflow", op="run", name_or_id="e2e_slow", context_id=sid, run_id="e2e_first"
        ),
        client.call(
            "workflow", op="run", name_or_id="e2e_slow", context_id=sid, run_id="e2e_second"
        ),
    )
    assert "context_busy" in {one[0].get("reason"), two[0].get("reason")}, (one[0], two[0])
    await client.ok("workflow", op="delete", template_id="e2e_slow")
    slow["template_id"] = "e2e_control"
    slow["steps"][1]["action"]["timeout_s"] = 2
    await client.ok("workflow", op="save", template=slow)
    run = asyncio.create_task(
        client.call(
            "workflow", op="run", name_or_id="e2e_control", context_id=sid, run_id="controlled"
        )
    )
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        active = await client.ok("browser", op="status")
        if "controlled" in active["runs"]:
            break
    else:
        raise AssertionError("workflow never appeared in public status")
    await client.ok("workflow", op="pause", run_id="controlled")
    paused = await client.ok("browser", op="status")
    assert paused["runs"]["controlled"]["status"] == "paused"
    await client.ok("workflow", op="resume", run_id="controlled")
    await client.ok("workflow", op="cancel", run_id="controlled")
    result, _ = await asyncio.wait_for(run, timeout=5)
    assert result["status"] in {"cancelled", "fail", "error"}, result
    await client.ok("workflow", op="delete", template_id="e2e_control")
    await client.ok("workflow", op="record_start", page_id=pid)
    await client.ok(
        "locator", op="fill", page_id=pid, target={"css": "#text-input"}, value="recorded"
    )
    recorded = await client.ok("workflow", op="record_stop", template_id="e2e_recorded")
    assert recorded["recorded_steps"] >= 1
    await client.ok("workflow", op="stats")
    await client.ok("workflow", op="delete", template_id="e2e_recorded")


async def profile_copy(client, data):
    # This source was created by the test itself, never the user's profile.
    await client.ok("browser", op="close")
    await client.ok(
        "browser",
        op="profile_copy",
        source=str(Path(data, "browser-profiles", "primary")),
        name="e2e-copy",
        tier="strict",
    )
    await client.ok("browser", op="profile_list")
    copied = await client.ok("context", op="create", profile="e2e-copy")
    assert copied["container"] == "default" and copied["active_page_id"], copied
    await client.ok("browser", op="close", profile="e2e-copy")
    await client.ok("browser", op="profile_delete", name="e2e-copy")


async def capture_images(client, pid, env, options, report):
    await client.ok(
        "page",
        op="navigate",
        page_id=pid,
        url=(WEBAPP / "acceptance.html").as_uri(),
    )
    images = []
    for scope in (
        {},
        {"full_page": True},
        {"selector": "#delayed", "save_to": env["AGENT_GAVEL_DATA_DIR"]},
    ):
        metadata, wire = await client.call("screenshot", page_id=pid, **scope)
        assert not wire.get("isError"), metadata
        blocks = [block for block in wire["content"] if block["type"] == "image"]
        assert len(blocks) == 1 and blocks[0]["mimeType"] == "image/png", [
            block["type"] for block in wire["content"]
        ]
        png = base64.b64decode(blocks[0]["data"], validate=True)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", png[16:24])
        assert (metadata["width"], metadata["height"]) == (width, height)
        assert width > 0 and height > 0 and metadata["artifact_id"]
        if "save_to" in scope:
            assert metadata["exported"] and metadata["exported_filename"]
        images.append({"scope": scope, "width": width, "height": height, "bytes": len(png)})
        if options.output:
            image_dir = Path(options.output).with_suffix("")
            image_dir = image_dir.with_name(image_dir.name + "-images")
            await asyncio.to_thread(image_dir.mkdir, parents=True, exist_ok=True)
            image_path = image_dir / f"capture-{len(images)}.png"
            await asyncio.to_thread(image_path.write_bytes, png)
            images[-1]["path"] = str(image_path)
        (Path(env["AGENT_GAVEL_DATA_DIR"]) / f"capture-{len(images)}.png").write_bytes(png)
    assert images[1]["height"] > images[0]["height"], images
    assert images[2]["height"] < images[0]["height"], images
    report["screenshots"] = images
    return metadata["artifact_id"]
