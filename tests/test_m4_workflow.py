"""M4 工作流验收：录制/编译/单调用重放/暂停恢复取消/局部修复 + 诊断。

docs/tech-plan.md §8（M4）与 §5.15。
"""

import asyncio

from agent_gavel.runtime import get_runtime


async def _session_on(gavel_tools, base, path="/basic.html"):
    r = await gavel_tools["session_create"]()
    await gavel_tools["page_navigate"](page_id=r["page_id"], url=f"{base}{path}")
    return r["session_id"], r["context_id"], r["page_id"]


async def test_record_compile_run(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)
    await T["workflow_record_start"](page_id=pid)
    await T["locator_fill"](page_id=pid,
                            target={"by": "css", "value": "#text-input"},
                            value="hello")
    await T["locator_click"](page_id=pid,
                             target={"by": "css", "value": "#btn"})
    stop = await T["workflow_record_stop"](site="testsite", desc="demo",
                                           name="fill_click",
                                           parameterize={"QUERY": "hello"})
    assert stop["status"] == "ok"
    assert stop["recorded_steps"] == 2
    tid = stop["template_id"]

    lst = await T["workflow_list"]()
    assert any(w["template_id"] == tid for w in lst["items"])

    res = await T["workflow_run"](name_or_id=tid, params={"QUERY": "world"})
    assert res["status"] == "pass", res
    rd = await T["page_read"](page_id=pid, page_features={
        "v": "document.querySelector('#text-input').value",
        "o": "document.querySelector('#out').innerText"})
    assert rd["features"]["v"] == "world"  # 参数替换生效
    assert rd["features"]["o"] == "clicked"


async def test_workflow_validate(gavel_tools):
    T = gavel_tools
    bad = await T["workflow_validate"](template={"template_id": "x", "steps": []})
    assert bad["valid"] is False and bad["errors"]
    good = await T["workflow_validate"](template={
        "template_id": "x", "steps": [{"id": "a", "action": {"type": "navigate",
                                                             "url": "https://e.com"}}]})
    assert good["valid"] is True


async def test_repair_republishes(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)
    tmpl = {"template_id": "testsite_repair", "site": "testsite", "desc": "repair",
            "steps": [{"id": "go", "action": {
                "type": "click", "target": {"by": "css", "value": "Go"}}}],
            "failure_policy": {"retry": "safe_only",
                               "repair": "explore_local_step"}}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="testsite_repair",
                                  verbosity="steps")
    assert res["status"] == "pass", res
    assert res.get("republished"), res
    assert res["republished"]["version"] == 2
    # 修复后重发不能把刚记录的 stats 冲掉
    st = (await T["workflow_stats"]())["stats"]["testsite_repair"]
    assert st["success_count"] == 1 and st["last_status"] == "pass", st
    rd = await T["page_read"](page_id=pid, page_features={
        "o": "document.querySelector('#out').innerText"})
    assert rd["features"]["o"] == "clicked"  # 修复后确实点了


async def test_requires_confirmation_blocked(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)
    tmpl = {"template_id": "testsite_save", "site": "testsite", "desc": "save",
            "steps": [{"id": "s", "action": {
                "type": "click", "target": {"by": "css", "value": "#save"}}}]}
    await T["workflow_save"](template=tmpl)
    blocked = await T["workflow_run"](name_or_id="testsite_save")
    assert blocked["status"] == "blocked", blocked
    assert blocked["reason"] == "requires_confirmation"
    rd = await T["page_read"](page_id=pid, page_features={
        "o": "document.querySelector('#out').innerText"})
    assert rd["features"]["o"] == ""  # 未确认，没执行
    ok = await T["workflow_run"](name_or_id="testsite_save", confirm=True)
    assert ok["status"] == "pass", ok


async def test_pause_resume(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    tmpl = {"template_id": "testsite_slow", "site": "testsite", "desc": "slow",
            "steps": [{"id": "s1", "action": {"type": "navigate",
                                              "url": f"{base}/slow"}},
                      {"id": "s2", "action": {"type": "navigate",
                                              "url": f"{base}/basic.html"}}]}
    await T["workflow_save"](template=tmpl)
    task = asyncio.create_task(T["workflow_run"](name_or_id="testsite_slow",
                                                 verbosity="steps"))
    await asyncio.sleep(0.4)
    await T["workflow_pause"]()
    rt = get_runtime()
    for _ in range(50):
        if any(x.status == "paused" for x in rt.runs.values()):
            break
        await asyncio.sleep(0.1)
    assert any(x.status == "paused" for x in rt.runs.values())
    await T["workflow_resume"]()
    res = await task
    assert res["status"] == "pass", res
    assert res["steps_completed"] == 2


async def test_cancel(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    tmpl = {"template_id": "testsite_cancel", "site": "testsite", "desc": "cancel",
            "steps": [{"id": "s1", "action": {"type": "navigate",
                                              "url": f"{base}/slow"}},
                      {"id": "s2", "action": {"type": "navigate",
                                              "url": f"{base}/basic.html"}}]}
    await T["workflow_save"](template=tmpl)
    task = asyncio.create_task(T["workflow_run"](name_or_id="testsite_cancel"))
    await asyncio.sleep(0.3)
    await T["workflow_cancel"]()
    res = await task
    assert res["status"] == "cancelled", res


async def test_trace(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, cid, pid = await _session_on(T, base)
    assert (await T["trace_start"](context_id=cid))["tracing"] is True
    await T["page_navigate"](page_id=pid, url=f"{base}/forms.html")
    st = await T["trace_stop"](context_id=cid)
    assert st["status"] == "ok" and st["size_bytes"] > 0
    meta = await T["trace_get"](artifact_id=st["artifact_id"])
    assert meta["kind"] == "trace"


async def test_network_console_errors(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, cid, pid = await _session_on(T, base, "/basic.html")
    await T["network_start"](context_id=cid)
    await T["page_navigate"](page_id=pid, url=f"{base}/forms.html")
    ns = await T["network_stop"](context_id=cid)
    assert any(r["type"] == "response" and "forms.html" in r.get("url", "")
               for r in ns["records"])

    await T["page_navigate"](page_id=pid, url=f"{base}/errors.html")
    await T["locator_click"](page_id=pid,
                             target={"by": "css", "value": "#boom"})
    await asyncio.sleep(0.3)
    ce = await T["console_get"](page_id=pid)
    assert any("boom-console" in (e.get("text") or "")
               for e in ce["items"])
    pe = await T["page_errors"](page_id=pid)
    assert pe["total"] >= 1


async def test_performance_metrics(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)
    await T["page_read"](page_id=pid, page_features={"t": "document.title"})
    pm = await T["performance_metrics"]()
    assert pm["status"] == "ok"
    assert "navigate" in pm["metrics"] or "page_read" in pm["metrics"]
