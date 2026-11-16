"""单步降轮数：读文本时直接给出可点目标，导航时直接带回正文。

目标是省掉 text → find_text → click 这类机械往返，不改变既有工具面。
"""


async def _open(T, base, path="/basic.html"):
    r = await T["context_create"]()
    pid = r["active_page_id"]
    await T["page_navigate"](page_id=pid, url=f"{base}{path}")
    return pid


async def test_text_returns_clickable_anchors(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    pid = await _open(T, base)

    txt = await T["page_text"](page_id=pid)
    anchors = txt.get("anchors") or []
    assert anchors, txt
    go = [a for a in anchors if a["text"] == "Go"]
    assert go and go[0]["target"], txt

    # 直接拿 text 里的 target 去点，不再需要 find_text
    res = await T["locator_click"](page_id=pid, target=go[0]["target"])
    assert res["status"] == "ok", res
    out = await T["page_read"](
        page_id=pid, page_features={"o": "document.querySelector('#out').innerText"}
    )
    assert out["features"]["o"] == "clicked"


async def test_navigate_can_return_text_and_anchors(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    r = await T["context_create"]()
    pid = r["active_page_id"]
    nav = await T["page_navigate"](page_id=pid, url=f"{base}/basic.html", text_chars=2000)
    assert nav["status"] == "ok", nav
    assert "Hello" in (nav.get("text") or ""), nav
    assert nav.get("anchors"), nav
    assert any(a["text"] == "Go" for a in nav["anchors"]), nav


async def test_text_anchors_respect_selector_scope(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    pid = await _open(T, base)
    txt = await T["page_text"](page_id=pid, selector="#out")
    # 空容器里没有可点元素，不应凭空产生锚点
    assert not txt.get("anchors"), txt
