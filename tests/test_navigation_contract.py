"""导航管线的契约：open/navigate 对齐、软拦截、正文默认返回、compact 去噪。

这些用例直接走 catalog（detail 可控），因为旧名 fixture 固定用 detail=full。
"""

from agent_gavel.tools import create_catalog


async def _call(tool, **payload):
    return (await create_catalog().call(tool, payload)).result


async def _session(gavel_tools):
    r = await gavel_tools["context_create"]()
    return r["context_id"], r["active_page_id"]


async def test_navigate_compact_returns_text_without_diff_or_anchors(gavel_tools, webapp_server):
    await _session(gavel_tools)
    r = await _call("page", op="navigate", url=f"{webapp_server}/basic.html")
    assert r["status"] == "ok", r
    assert "Hello" in r["text"]  # text_chars 默认 4000
    assert "diff" not in r  # 导航类 compact 不带 diff
    assert "anchors" not in r  # 导航类 compact 不带 anchors


async def test_navigate_full_keeps_diff_and_anchors(gavel_tools, webapp_server):
    await _session(gavel_tools)
    r = await _call("page", op="navigate", url=f"{webapp_server}/basic.html", detail="full")
    assert r.get("diff"), r
    assert r.get("anchors"), r


async def test_open_shares_navigate_contract(gavel_tools, webapp_server):
    cid, _pid = await _session(gavel_tools)
    r = await _call(
        "page", op="open", context_id=cid, url=f"{webapp_server}/basic.html", text_chars=200
    )
    assert r["status"] == "ok", r
    assert r["final_url"].endswith("/basic.html")
    assert "Hello" in r["text"]


async def test_open_without_session_is_lazy(gavel_tools, webapp_server):
    r = await _call("page", op="open", url=f"{webapp_server}/basic.html", text_chars=100)
    assert r["status"] == "ok", r
    assert r["page_id"]


async def test_soft_block_is_flagged(gavel_tools, webapp_server):
    await _session(gavel_tools)
    r = await _call("page", op="navigate", url=f"{webapp_server}/auth/ipauth")
    assert r["status"] == "ok", r  # 工具没崩
    assert r["page_status"] == "blocked", r
    assert r["warning"] == "access_restricted", r


async def test_sparse_content_requires_attachment(gavel_tools, webapp_server):
    """字少且有附件才提示 sparse；纯短页面不误报。"""
    await _session(gavel_tools)
    plain = await _call("page", op="navigate", url=f"{webapp_server}/tiny.html")
    assert "sparse_content" not in (plain.get("warning") or ""), plain

    with_file = await _call("page", op="navigate", url=f"{webapp_server}/attachment_page.html")
    assert with_file["warning"] == "sparse_content", with_file


async def test_snapshot_interactive_has_href_without_noise(gavel_tools, webapp_server):
    await _session(gavel_tools)
    await _call("page", op="navigate", url=f"{webapp_server}/basic.html")
    r = await _call("page", op="snapshot", level="interactive")
    link = next(e for e in r["elements"] if e.get("text") == "multi")
    assert link["href"].endswith("multiple_pages.html"), link
    assert "bounds" not in link and "candidates" not in link, link
    assert link["anchor"]["by"] == "css", link


async def test_navigate_to_attachment_returns_artifact(gavel_tools, webapp_server):
    cid, pid = await _session(gavel_tools)
    await _call("page", op="navigate", url=f"{webapp_server}/basic.html")
    r = await _call("page", op="navigate", url=f"{webapp_server}/attachment")
    assert r["status"] == "ok", r
    assert r["artifact_id"].startswith("artifact_"), r
    assert r["warning"] == "navigated_to_download", r
    assert "artifact(op=read_text" in r["hint"], r
    # 附件元数据要够模型判断能不能 read_text
    assert r["suggested_filename"] == "notice.pdf", r
    assert r["mime_type"] == "application/pdf", r
    # 页面没有跳转：final_url 仍是当前页
    assert r["final_url"].endswith("/basic.html"), r
    assert r["navigated"] is False, r
    assert r["download_url"].endswith("/attachment"), r


async def test_text_falls_back_when_main_is_empty(gavel_tools, webapp_server):
    """空壳 <main> 不能采纳：回退到去噪后的整页正文。"""
    await _session(gavel_tools)
    r = (
        await _call("page", op="text", url=None)
        if False
        else await _call("page", op="navigate", url=f"{webapp_server}/empty_main.html")
    )
    assert "真正的正文在这里" in r["text"], r
    assert "站点导航" not in r["text"], r  # header/nav 已剔除


async def test_thresholds_use_cleaned_length(gavel_tools, webapp_server):
    """正文长度按去噪后的内容算，不被导航/页脚垫长。"""
    await _session(gavel_tools)
    r = await _call("page", op="navigate", url=f"{webapp_server}/nav_heavy.html")
    # 整页 innerText 有几百字导航，去噪后只剩 1 字：长度按去噪后算，且不误报 sparse
    assert r["text_len"] < 100, r
    assert "sparse_content" not in (r.get("warning") or ""), r


async def test_snapshot_compact_keeps_input_affordances(gavel_tools, webapp_server):
    await _session(gavel_tools)
    await _call("page", op="navigate", url=f"{webapp_server}/inputs.html")
    r = await _call("page", op="snapshot", level="interactive")
    by_id = {e["anchor"]["value"]: e for e in r["elements"] if e.get("anchor")}
    search = by_id["#kw"]
    assert search["type"] == "search", search
    assert search["placeholder"] == "搜索通知", search
    assert search["text"] == "搜索通知", search  # text 为空时用 placeholder 兜底
    agree = by_id["#agree"]
    assert agree["type"] == "checkbox", agree
    assert agree["text"] == "同意条款", agree  # aria 兜底
    upload = by_id["#file"]
    assert upload["type"] == "file", upload


async def test_open_without_session_reuses_default(gavel_tools, webapp_server):
    """连续无参数的 open 必须复用同一个默认上下文。"""
    a = await _call("page", op="open", url=f"{webapp_server}/basic.html", text_chars=50)
    b = await _call("page", op="open", url=f"{webapp_server}/tiny.html", text_chars=50)
    assert a["context_id"] == b["context_id"], (a, b)
    assert a["context_id"] == b["context_id"], (a, b)
    status = await gavel_tools["browser_status"]()
    assert status["counts"]["contexts"] == 1, status
