"""M2 基础 DOM API 验收（docs/tech-plan.md §9 M2）。"""


async def test_navigate_read_text_links(page):
    tools, pid, base = page
    r = await tools["page_read"](page_id=pid,
                                 page_features={"h": "document.querySelector('#heading').innerText"})
    assert r["status"] == "ok"
    assert r["features"]["h"] == "Hello"

    t = await tools["page_text"](page_id=pid, mode="text")
    assert "Hello" in t["text"]

    links = await tools["page_links"](page_id=pid)
    hrefs = [x["href"] for x in links["items"]]
    assert any("basic.html" in h for h in hrefs)


async def test_explore_anchor(page):
    tools, pid, _ = page
    r = await tools["page_explore"](page_id=pid, text_contains="Go")
    assert r["status"] == "ok"
    item = r["items"][0]
    assert item["anchor"] == {"kind": "css", "value": "#btn"}


async def test_fill_assert_pass(page):
    tools, pid, _ = page
    r = await tools["locator_fill"](
        page_id=pid, target={"by": "css", "value": "#text-input"}, value="流浪地球",
        assertions={"v": {"read": "value", "selector": "#text-input",
                          "op": "eq", "value": "流浪地球"}})
    assert r["status"] == "pass"
    assert r["assertions"][0]["passed"] is True


async def test_click_assert_pass(page):
    tools, pid, _ = page
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#btn"},
        assertions={"out": {"selector": "#out", "op": "eq", "value": "clicked"}})
    assert r["status"] == "pass"


async def test_click_assert_ambiguous(page):
    tools, pid, _ = page
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#btn"},
        assertions={"out": {"selector": "#out", "op": "eq", "value": "WRONG"}},
        wait_s=0.3)
    assert r["status"] == "ambiguous"  # 页面确实变了但断言没满足


async def test_assertion_fail_no_change(page):
    tools, pid, _ = page
    r = await tools["locator_hover"](
        page_id=pid, target={"by": "css", "value": "#box"},
        assertions={"out": {"selector": "#out", "op": "eq", "value": "WRONG"}},
        wait_s=0.2)
    assert r["status"] == "fail"  # 页面没变且断言不满足


async def test_strict_duplicate(page):
    tools, pid, _ = page
    r = await tools["locator_click"](page_id=pid,
                                     target={"by": "text", "value": "Same"})
    assert r["status"] == "error"
    assert r["reason"] == "locator_not_unique"
    # 用 nth 收窄可点
    ok = await tools["locator_click"](
        page_id=pid, target={"by": "text", "value": "Same", "nth": 0})
    assert ok["status"] == "ok"


async def test_chinese_type(page):
    tools, pid, _ = page
    await tools["locator_fill"](page_id=pid,
                                target={"by": "css", "value": "#text-input"},
                                value="")
    await tools["locator_type"](page_id=pid,
                                target={"by": "css", "value": "#text-input"},
                                text="中文测试")
    r = await tools["page_read"](page_id=pid,
                                 page_features={"v": "document.querySelector('#text-input').value"})
    assert r["features"]["v"] == "中文测试"


async def test_ctrl_a_real_semantics(page):
    tools, pid, _ = page
    await tools["locator_fill"](page_id=pid,
                                target={"by": "css", "value": "#text-input"},
                                value="abc")
    await tools["locator_press"](page_id=pid,
                                 target={"by": "css", "value": "#text-input"},
                                 keys="Control+A")
    await tools["locator_type"](page_id=pid,
                                target={"by": "css", "value": "#text-input"},
                                text="X")
    r = await tools["page_read"](page_id=pid,
                                 page_features={"v": "document.querySelector('#text-input').value"})
    assert r["features"]["v"] == "X"  # 全选后被替换，而非追加


async def test_expect_pass_and_fail(page):
    tools, pid, _ = page
    ok = await tools["expect"](page_id=pid,
                               checks={"h": {"selector": "#heading",
                                             "op": "eq", "value": "Hello"}},
                               wait_s=0.5)
    assert ok["status"] == "pass"
    bad = await tools["expect"](page_id=pid,
                                checks={"x": {"selector": "#nope",
                                              "op": "exists"}}, wait_s=0.3)
    assert bad["status"] == "fail"
    assert bad["assertions"][0]["passed"] is False


async def test_wait_selector_and_url(page):
    tools, pid, base = page
    r = await tools["page_wait_for_selector"](page_id=pid, selector="#heading",
                                              timeout_s=3)
    assert r["status"] == "ok"
    r2 = await tools["page_wait_for_url"](page_id=pid, url="**/basic.html",
                                          timeout_s=3)
    assert r2["status"] == "ok"


async def test_delayed_poll(page):
    tools, pid, base = page
    await tools["page_navigate"](page_id=pid, url=f"{base}/delayed.html")
    r = await tools["expect"](page_id=pid,
                              checks={"s": {"selector": "#status",
                                            "op": "eq", "value": "ready"}},
                              wait_s=3.0)
    assert r["status"] == "pass"


async def test_screenshot_and_pdf(page):
    tools, pid, _ = page
    ss = await tools["page_screenshot"](page_id=pid)
    assert ss["status"] == "ok"
    assert ss["artifact_id"].startswith("artifact_")
    assert ss["width"] > 0 and ss["coordinate_width"] == ss["width"]

    pdf = await tools["page_pdf"](page_id=pid)
    assert pdf["status"] == "ok"
    assert pdf["size_bytes"] > 0


async def test_read_no_match(page):
    tools, pid, _ = page
    r = await tools["page_read"](page_id=pid, page_features={"x": "1+1"},
                                 selector="#nope")
    assert r["status"] == "no_match"


async def test_snapshot(page):
    tools, pid, _ = page
    r = await tools["page_snapshot"](page_id=pid, level="summary")
    assert r["status"] == "ok"
    assert r["title"] == "Basic Page"
    assert r["counts"]["buttons"] >= 3
