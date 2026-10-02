"""决策 B 的回归测试：B1 安全重试 / B2 局部 diff 三态 / B3 事件优先等待。"""

import time


async def _session_on(tools, base, path="/basic.html"):
    r = await tools["session_create"]()
    await tools["page_navigate"](page_id=r["page_id"], url=f"{base}{path}")
    return r["session_id"], r["context_id"], r["page_id"]


# ---------------- B1 ----------------

async def test_safe_step_retries_once(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    tmpl = {"template_id": "ts_retry_safe", "site": "ts", "desc": "r",
            "steps": [{"id": "go", "action": {
                "type": "click", "target": {"by": "css", "value": "Go"}}}],
            "failure_policy": {"retry": "safe_only", "repair": "none"}}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="ts_retry_safe", verbosity="steps")
    assert res["status"] in ("fail", "error"), res
    step = res["steps"][0]
    assert step.get("retried") is True
    assert step.get("retry_failed") is True


async def test_side_effect_step_not_retried(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    await _session_on(T, base)
    # "提交" 命中副作用关键词 → 需确认/不可重试；确认后执行，失败也不得重试
    tmpl = {"template_id": "ts_retry_danger", "site": "ts", "desc": "d",
            "steps": [{"id": "s", "action": {
                "type": "click", "target": {"by": "css", "value": "提交"}}}],
            "failure_policy": {"retry": "safe_only", "repair": "none"}}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="ts_retry_danger", confirm=True,
                                  verbosity="steps")
    assert res["status"] in ("fail", "error"), res
    assert "retried" not in res["steps"][0]


# ---------------- B2 ----------------

async def test_ambiguous_returns_diff_evidence(page):
    tools, pid, _ = page
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#btn"},
        assertions={"out": {"selector": "#out", "op": "eq", "value": "WRONG"}},
        wait_s=0.3)
    assert r["status"] == "ambiguous", r
    d = r.get("diff")
    assert d and d.get("meaningful") is True, r
    assert d.get("samples_added") or d.get("samples_removed")


async def test_no_change_is_fail_without_diff(page):
    tools, pid, _ = page
    # 断言恒假、且动作没有改变页面（#dup1 点了什么都不做）→ 不通过，且不返回 diff
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#dup1"},
        assertions={"out": {"selector": "#out", "op": "eq", "value": "WRONG"}},
        wait_s=0.2)
    assert r["status"] == "fail", r
    assert "diff" not in r


# ---------------- B3 ----------------

async def test_assertion_wakes_before_timeout(page):
    tools, pid, _ = page
    t0 = time.monotonic()
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#fetch-slow"},
        assertions={"f": {"selector": "#fetch-out", "op": "contains",
                          "value": "slow"}}, wait_s=10)
    dt = time.monotonic() - t0
    assert r["status"] == "pass", r
    assert dt < 6, dt  # /slow 服务端 2s，事件唤醒不应等满 10s


def test_event_predicate_skips_frames():
    from agent_gavel.backends.playwright_backend import PlaywrightBackend
    be = PlaywrightBackend()
    assert be._event_predicate([{"selector": "#x", "op": "exists",
                                 "frame": "#f"}]) is None
    assert be._event_predicate([{"selector": "#x", "op": "exists"}]) is not None
