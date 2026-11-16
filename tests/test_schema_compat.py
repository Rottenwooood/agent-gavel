"""工具 schema 的跨客户端兼容性。

Anthropic 会拒绝 input_schema 顶层的 oneOf/allOf/anyOf：
"input_schema does not support oneOf, allOf, or anyOf at the top level"。
条件必填改由服务端逐 op 校验，客户端提示写进工具描述。
"""


def _schemas():
    from agent_gavel.tools import create_catalog

    return create_catalog().schemas()


def test_no_top_level_union_in_any_tool():
    bad = []
    for tool in _schemas():
        for keyword in ("oneOf", "allOf", "anyOf"):
            if keyword in tool["inputSchema"]:
                bad.append((tool["name"], keyword))
    assert not bad, bad


def test_every_tool_schema_is_a_plain_object():
    for tool in _schemas():
        schema = tool["inputSchema"]
        assert schema["type"] == "object", tool["name"]
        assert isinstance(schema.get("properties"), dict), tool["name"]


def test_operation_labels_carry_required_fields():
    """去掉 union 后，必填信息必须能在描述里读到。"""
    by_name = {tool["name"]: tool["description"] for tool in _schemas()}
    assert "navigate(url!)" in by_name["page"]
    assert "check[checks|assertions]" in by_name["assertion"]
    assert "save(template!)" in by_name["workflow"]


async def test_per_op_requirements_still_enforced(gavel_tools):
    missing_url = await gavel_tools["page_navigate"]()
    assert missing_url["status"] == "error", missing_url
    assert missing_url["reason"] == "bad_args", missing_url
    assert "url" in (missing_url.get("hint") or ""), missing_url

    neither = await gavel_tools["expect"]()
    assert neither["status"] == "error", neither
    assert neither["reason"] == "bad_args", neither
    assert "checks" in (neither.get("hint") or ""), neither


async def test_cross_op_field_is_rejected(gavel_tools):
    r = await gavel_tools["locator_click"](
        page_id="page_1",
        target={"by": "css", "value": "#x"},
        url="http://example.com",
    )
    assert r["status"] == "error" and r["reason"] == "bad_args", r
    assert "url" in (r.get("hint") or ""), r


# ---------------- 错误提示必须用 0.5 的分组工具名 ----------------

_STALE = (
    "page_open",
    "page_list",
    "page_text",
    "page_read",
    "page_links",
    "page_snapshot",
    "page_explore",
    "page_find_text",
    "page_wait_for",
    "context_create",
    "context_list",
    "session_create",
    "session_list",
    "artifact_get",
    "artifact_list",
    "artifact_read_text",
    "workflow_run",
    "workflow_save",
    "workflow_get",
    "workflow_list",
    "workflow_delete",
    "browser_profile_delete",
    "locator_click",
    "locator_fill",
)


def test_hints_use_grouped_tool_names():
    """报错提示里不能再出现 0.4 的扁平工具名，否则模型会照着不存在的名字调。"""
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parents[1] / "agent_gavel"
    bad = []
    for path in pkg.rglob("*.py"):
        rel = path.relative_to(pkg).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "hint=" not in line:
                continue
            for name in _STALE:
                if name in line:
                    bad.append((rel, lineno, name))
    assert not bad, bad


async def test_missing_page_hint_points_at_grouped_op(gavel_tools):
    r = await gavel_tools["page_get"](page_id="page_nope")
    assert r["status"] == "error", r
    assert "page(op=open)" in (r.get("hint") or ""), r


async def test_links_is_part_of_text(gavel_tools, webapp_server):
    """links 不再单独占一个 op；text(mode=links) 同时给 items 与 total。"""
    r = await gavel_tools["context_create"]()
    await gavel_tools["page_navigate"](page_id=r["active_page_id"], url=f"{webapp_server}/basic.html")

    ok = await gavel_tools["page_text"](page_id=r["active_page_id"], mode="links")
    assert ok["status"] == "ok", ok
    assert ok["links"] and ok["total"] >= len(ok["links"]), ok

    from agent_gavel.tools import create_catalog

    gone = (await create_catalog().call("page", {"op": "links", "page_id": r["active_page_id"]})).result
    assert gone["status"] == "error", gone
    assert gone["reason"] == "unknown_operation", gone
    assert "text, mode=links" in (gone.get("hint") or ""), gone


def test_headline_lists_ops_so_no_lookup_is_needed():
    """目录条目只露第一行；第一行必须带全 op，模型才能不查文档直接调。"""
    from agent_gavel.tools import create_catalog
    from agent_gavel.tools.catalog import COMMON_OPS

    catalog = create_catalog()
    tools = catalog.schemas()
    from agent_gavel.tools.catalog import SINGLE_TOOLS

    for tool in tools:
        if tool["name"] in SINGLE_TOOLS:
            continue
        headline = tool["description"].split("\n")[0]
        assert "op: " in headline, (tool["name"], headline[:80])
        ops = [operation.op for operation in catalog.groups[tool["name"]] if operation.op]
        for op in ops:
            assert op in headline, (tool["name"], op)
        for op in COMMON_OPS.get(tool["name"], ()):
            assert op in ops, (tool["name"], op)
