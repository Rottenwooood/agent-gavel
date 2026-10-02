"""工作流契约回归：原子工具 / 录制 / 编译 / 重放必须共享同一套动作与验证契约。

覆盖 codex 发布阻断清单：
  #1 expect 失败不得报整体成功
  #2 录制保留原断言；编译器保留录制 checkpoint
  #3 编译器不得无条件删除 hover/focus
  #4 type(text=...) 必须真的输入
  #5 截图步骤传对对象
  #6 template_id 不得路径穿越
  #8 参数替换不得前缀冲突
  #7 普通 click 不得默认可重试（幂等性按动作类型判定）
"""

import pytest

from agent_gavel.runtime.errors import GavelError
from agent_gavel.workflows import loader
from agent_gavel.workflows import schema as S


async def _session_on(tools, base, path="/basic.html"):
    r = await tools["session_create"]()
    await tools["page_navigate"](page_id=r["page_id"], url=f"{base}{path}")
    return r["session_id"], r["context_id"], r["page_id"]


# ---------------- #1 expect 失败 ----------------

async def test_expect_failure_fails_workflow(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    tmpl = {"template_id": "ts_expect_fail", "site": "ts", "desc": "e",
            "steps": [{"id": "e", "action": {
                "type": "expect",
                "checks": {"t": {"read": "title", "op": "eq",
                                 "value": "NOPE"}},
                "wait_s": 0.2}}]}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="ts_expect_fail",
                                  verbosity="steps")
    assert res["status"] == "fail", res
    assert res["steps"][0]["status"] == "fail", res


# ---------------- #2 录制保留断言 ----------------

async def test_record_keeps_assertions_as_checkpoint(gavel_tools,
                                                     webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base)
    await T["workflow_record_start"](page_id=pid)
    await T["locator_fill"](page_id=pid,
                            target={"by": "css", "value": "#text-input"},
                            value="Alice",
                            assertions={"v": {"read": "value",
                                              "selector": "#text-input",
                                              "op": "eq", "value": "Alice"}})
    stop = await T["workflow_record_stop"](site="ts", name="assert_rec")
    assert stop["status"] == "ok", stop
    got = await T["workflow_get"](name_or_id=stop["template_id"])
    steps = got["template"]["steps"]
    assert any(s.get("checkpoint") for s in steps), steps  # 断言没被丢


# ---------------- #3 不删 hover/focus ----------------

async def test_compile_keeps_hover(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base)
    await T["workflow_record_start"](page_id=pid)
    await T["locator_hover"](page_id=pid,
                             target={"by": "css", "value": "#btn"})
    stop = await T["workflow_record_stop"](site="ts", name="hover_rec")
    got = await T["workflow_get"](name_or_id=stop["template_id"])
    types = [s["action"]["type"] for s in got["template"]["steps"]]
    assert "hover" in types, types


# ---------------- #4 type(text=...) 真的输入 ----------------

async def test_type_text_actually_types(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base)
    tmpl = {"template_id": "ts_type_text", "site": "ts", "desc": "t",
            "steps": [{"id": "t", "action": {
                "type": "type", "target": {"by": "css", "value": "#text-input"},
                "text": "hello"}}]}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="ts_type_text")
    assert res["status"] == "pass", res
    rd = await T["page_read"](page_id=pid, page_features={
        "v": "document.querySelector('#text-input').value"})
    assert rd["features"]["v"] == "hello", rd


# ---------------- #5 截图步骤传对对象 ----------------

async def test_workflow_screenshot_step(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    tmpl = {"template_id": "ts_shot", "site": "ts", "desc": "s",
            "steps": [{"id": "s", "action": {"type": "screenshot"}}]}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="ts_shot")
    assert res["status"] == "pass", res
    assert any(a["kind"] == "screenshot" for a in res["artifacts"]), res


# ---------------- #6 template_id 路径穿越 ----------------

async def test_bad_template_id_rejected(gavel_tools):
    T = gavel_tools
    bad = await T["workflow_save"](template={
        "template_id": "../escaped", "site": "x", "desc": "",
        "steps": [{"id": "a", "action": {"type": "navigate",
                                         "url": "https://e.com"}}]})
    assert bad["status"] == "error" and bad["reason"] == "invalid_template", bad
    with pytest.raises(GavelError):
        loader.save({"template_id": "../escaped", "steps": []})


# ---------------- #8 参数替换前缀冲突 ----------------

def test_substitute_no_prefix_conflict():
    assert S.substitute("$QUERY", {"QUERY": "hello"}) == "hello"
    # $QUERY 不得替换进 $QUERY_ID
    assert S.substitute("$QUERY_ID", {"QUERY": "hello"}) == "$QUERY_ID"
    assert S.substitute("$QUERY_ID", {"QUERY": "hello",
                                      "QUERY_ID": "7"}) == "7"
    assert S.substitute("$QUERY x", {"QUERY": "hello"}) == "hello x"
    # params 里没有的变量保持原样（不静默清空）
    assert S.substitute("$MISSING", {"QUERY": "hello"}) == "$MISSING"


# ---------------- #7 幂等性按动作类型判定 ----------------

def test_idempotency_defaults_by_action_type():
    tmpl = {"template_id": "ts_idem", "site": "ts", "desc": "",
            "steps": [
                {"id": "c", "action": {"type": "click",
                                       "target": {"by": "css", "value": "#btn"}}},
                {"id": "f", "action": {"type": "fill",
                                       "target": {"by": "css", "value": "#x"},
                                       "value": "v"}}]}
    norm, errors, _ = S.validate(tmpl)
    assert not errors, errors
    by_id = {s["id"]: s for s in norm["steps"]}
    # 普通点击：不因"没命中危险关键词"就默认可重试
    assert by_id["c"]["safe_to_retry"] is False
    assert by_id["c"]["idempotent"] is False
    assert by_id["c"]["requires_confirmation"] is False
    # 填值：天然幂等，可重试
    assert by_id["f"]["safe_to_retry"] is True
    assert by_id["f"]["idempotent"] is True
