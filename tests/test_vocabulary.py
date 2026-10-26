"""动作词表一致性：唯一来源 + 分类完整 + 分发实现齐全。"""

from agent_gavel.backends.playwright.actions import TARGET_HANDLERS
from agent_gavel.core.actions import ACTIONS, spec

TARGETLESS = {
    "navigate",
    "wait_for_selector",
    "wait_for_url",
    "wait_for_text",
    "expect",
    "screenshot",
    "pdf",
}


def test_every_action_is_classified():
    targets = {name for name, item in ACTIONS.items() if item.target_required}
    assert set(ACTIONS) == targets | TARGETLESS
    assert targets.isdisjoint(TARGETLESS)


def test_every_target_action_has_handler():
    targets = {name for name, item in ACTIONS.items() if item.target_required}
    assert targets == set(TARGET_HANDLERS)
    for name in targets:
        assert spec(name).type == name


def test_retry_scope_matches_classification():
    for name, item in ACTIONS.items():
        expected = "target" if item.target_required else "none"
        assert item.retry_scope == expected, name


# ---------------- 断言词表 ----------------

from agent_gavel.backends.playwright.assertion import COMPARATORS, READERS  # noqa: E402
from agent_gavel.backends.playwright.jsrender import render_js  # noqa: E402
from agent_gavel.core.checks import OPS, READ_KINDS, Check, from_checkpoint  # noqa: E402


def test_assertion_coverage():
    assert set(READERS) == set(READ_KINDS)
    assert set(COMPARATORS) == set(OPS)


def test_every_read_and_op_can_render_js():
    selectors = {"text", "value", "count", "html", "attr"}
    for read in READ_KINDS:
        check = Check(
            name=read,
            read=read,
            op="eq",
            expected="x",
            selector="#x" if read in selectors else None,
            attr="a" if read == "attr" else None,
            expr="1" if read == "expr" else None,
        )
        assert render_js(check) is not None, read
    for op in OPS:
        expected = 1 if op.startswith("count") or op in ("gt", "lt", "gte", "lte") else "x"
        check = Check(name="c", read="text", op=op, expected=expected, selector="#x")
        assert render_js(check) is not None, op


def test_checkpoint_forms():
    assert (
        from_checkpoint({"type": "text_contains", "value": "ok", "selector": "#s"})[0].read
        == "text"
    )
    assert from_checkpoint({"type": "url_contains", "value": "x"})[0].read == "url"
    assert from_checkpoint({"checks": {"title_contains": "x"}})[0].op == "contains"


async def test_tool_count_snapshot(gavel_tools):
    """工具面是契约的一部分：新增/删除必须显式更新快照。"""
    assert len(gavel_tools) == 95
