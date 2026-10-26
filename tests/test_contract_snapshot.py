"""对外响应契约快照：真实浏览器、真实工具调用。

这些用例不检查内部实现，只固定对外字段与三态语义。
重构若改变这些形状，必须在这里显式更新并在提交说明中写明原因。
"""

import asyncio

STATUS = {"ok", "pass", "fail", "ambiguous", "error", "blocked"}


def _shape(resp, *required):
    assert isinstance(resp, dict), resp
    missing = [k for k in required if k not in resp]
    assert not missing, f"缺少字段 {missing}: {resp}"
    assert resp.get("status") in STATUS, f"未登记的 status: {resp}"


async def test_contract_main_flow(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server

    s = await T["session_create"]()
    _shape(s, "session_id", "context_id", "page_id")
    pid, sid = s["page_id"], s["session_id"]

    n = await T["page_navigate"](page_id=pid, url=f"{base}/basic.html")
    _shape(n, "page_id", "final_url", "navigated", "title", "status_code")
    assert n["final_url"].endswith("/basic.html")
    assert n["title"] == "Basic Page"

    f = await T["locator_fill"](page_id=pid,
                                target={"by": "css", "value": "#text-input"},
                                value="contract")
    _shape(f, "page_id", "execution")
    assert f["status"] == "ok"

    c = await T["locator_click"](
        page_id=pid, target={"by": "css", "value": "#btn"},
        assertions={"out": {"read": "text", "selector": "#out",
                            "op": "eq", "value": "clicked"}})
    _shape(c, "assertions")
    assert c["status"] == "pass", c
    assert c["assertions"][0]["passed"] is True

    e = await T["expect"](page_id=pid, checks={"title_contains": "Basic"})
    _shape(e, "assertions")
    assert e["status"] == "pass", e

    tmpl = {
        "template_id": "contract_flow", "site": "contract", "desc": "contract",
        "steps": [
            {"id": "go", "action": {"type": "navigate",
                                    "url": f"{base}/basic.html"}},
            {"id": "fill", "action": {"type": "fill",
                                      "target": {"by": "css",
                                                 "value": "#text-input"},
                                      "value": "$QUERY"}},
            {"id": "click", "action": {"type": "click",
                                       "target": {"by": "css", "value": "#btn"}},
             "checkpoint": {"type": "text_eq", "selector": "#out",
                            "value": "clicked"}},
        ],
    }
    sv = await T["workflow_save"](template=tmpl)
    _shape(sv, "template_id", "version", "steps")
    va = await T["workflow_validate"](template=tmpl)
    _shape(va, "valid")
    assert va["valid"] is True, va

    run = await T["workflow_run"](name_or_id="contract_flow",
                                  params={"QUERY": "world"}, session_id=sid)
    _shape(run, "run_id", "steps_completed", "steps_total",
           "checkpoints_passed", "elapsed_ms")
    assert run["status"] == "pass", run
    assert run["steps_completed"] == 3
    assert run["checkpoints_passed"] == 1

    rd = await T["page_read"](page_id=pid, page_features={
        "v": "document.querySelector('#text-input').value"})
    assert rd["features"]["v"] == "world"

    art = await T["artifact_list"]()
    _shape(art, "items")

    st = await T["browser_status"]()
    _shape(st, "counts", "sessions")
    assert st["counts"]["pages"] >= 1


async def test_contract_errors(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    s = await T["session_create"]()
    pid, sid = s["page_id"], s["session_id"]
    await T["page_navigate"](page_id=pid, url=f"{base}/basic.html")

    nf = await T["locator_click"](page_id=pid,
                                  target={"by": "css", "value": "#does-not-exist"})
    _shape(nf, "reason", "error", "retryable")
    assert nf["status"] == "error"
    assert nf["reason"] == "locator_not_found"
    assert nf["retryable"] is True

    tmpl = {
        "template_id": "contract_precondition",
        "steps": [{"id": "a", "action": {"type": "navigate",
                                         "url": f"{base}/basic.html"}}],
        "preconditions": [{"type": "selector_exists", "selector": "#missing"}],
    }
    await T["workflow_save"](template=tmpl)
    run = await T["workflow_run"](name_or_id="contract_precondition",
                                  session_id=sid)
    _shape(run, "reason", "failed_preconditions")
    assert run["status"] == "blocked"
    assert run["reason"] == "precondition_failed"


async def test_contract_session_busy(gavel_tools, webapp_server):
    """同一 session 上的两个 run：一个执行，另一个必须明确报 busy。"""
    T, base = gavel_tools, webapp_server
    s = await T["session_create"]()
    sid = s["session_id"]
    tmpl = {
        "template_id": "contract_slow",
        "steps": [
            {"id": "go", "action": {"type": "navigate",
                                    "url": f"{base}/basic.html"}},
            {"id": "w", "action": {"type": "wait_for_selector",
                                   "selector": "#never-appears",
                                   "timeout_s": 3}},
        ],
    }
    await T["workflow_save"](template=tmpl)
    r1, r2 = await asyncio.gather(
        T["workflow_run"](name_or_id="contract_slow", session_id=sid),
        T["workflow_run"](name_or_id="contract_slow", session_id=sid),
    )
    reasons = {r1.get("reason"), r2.get("reason")}
    assert "session_busy" in reasons, (r1, r2)
    busy = r1 if r1.get("reason") == "session_busy" else r2
    _shape(busy, "detail")
    assert busy["detail"]["owner"]
