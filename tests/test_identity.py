"""资源身份验收：共享容器、录制归属、context 锁、歧义解析、产物索引并发。"""

import asyncio
import threading

from agent_gavel.runtime.artifacts import ArtifactStore


async def test_shared_container_identity(gavel_tools):
    T = gavel_tools
    a = await T["context_create"]()
    b = await T["context_create"]()
    assert a["context_id"] == b["context_id"]
    assert a["active_page_id"] == b["active_page_id"]
    listing = await T["page_list"]()
    page = [p for p in listing["items"] if p["page_id"] == a["active_page_id"]][0]
    assert page["context_id"] == a["context_id"]

    await T["context_close"](context_id=a["context_id"])
    await T["context_close"](context_id=b["context_id"])


async def test_recording_follows_context(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    a = await T["context_create"]()
    b = await T["context_create"]()
    await T["page_navigate"](page_id=b["active_page_id"], url=f"{base}/basic.html")
    await T["workflow_record_start"](context_id=b["context_id"], page_id=b["active_page_id"])
    await T["locator_fill"](
        page_id=b["active_page_id"], target={"by": "css", "value": "#text-input"}, value="identity"
    )
    stop = await T["workflow_record_stop"](site="identity", name="rec", desc="identity")
    assert stop["recorded_steps"] == 1, stop
    await T["workflow_delete"](template_id=stop["template_id"])

    await T["context_close"](context_id=a["context_id"])
    await T["context_close"](context_id=b["context_id"])


async def test_shared_context_run_is_exclusive(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    a = await T["context_create"]()
    b = await T["context_create"]()
    await T["page_navigate"](page_id=a["active_page_id"], url=f"{base}/basic.html")
    tmpl = {
        "template_id": "identity_busy",
        "steps": [
            {"id": "go", "action": {"type": "navigate", "url": f"{base}/basic.html"}},
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
    await T["workflow_save"](template=tmpl)
    r1, r2 = await asyncio.gather(
        T["workflow_run"](name_or_id="identity_busy", context_id=a["context_id"]),
        T["workflow_run"](name_or_id="identity_busy", context_id=b["context_id"]),
    )
    reasons = {r1.get("reason"), r2.get("reason")}
    assert "context_busy" in reasons, (r1, r2)

    await T["workflow_delete"](template_id="identity_busy")
    await T["context_close"](context_id=a["context_id"])
    await T["context_close"](context_id=b["context_id"])


async def test_page_resolution_is_explicit(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    a = await T["context_create"]()
    await T["page_navigate"](page_id=a["active_page_id"], url=f"{base}/basic.html")
    second = await T["context_create"](isolated=True)
    assert second["status"] == "ok"

    # 默认容器优先：后台多了隔离容器，不该让主流程报歧义
    r = await T["page_navigate"](url=f"{base}/basic.html")
    assert r["status"] == "ok", r
    assert r["context_id"] == a["context_id"], r

    await T["context_close"](context_id=second["context_id"])
    await T["context_close"](context_id=a["context_id"])


def test_artifact_index_concurrent_writes(tmp_path):
    store = ArtifactStore(root=str(tmp_path / "arts"))

    def worker(tag):
        for i in range(20):
            store.save_bytes(f"{tag}-{i}".encode(), kind="race")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    items = store.list(kind="race")
    assert len(items) == 80
