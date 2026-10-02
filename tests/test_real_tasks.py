"""真实任务级验收（不是单操作/生命周期）：多步业务闭环、后果验证、失败可解释。

站点在本仓库 tests/webapp/portal/ 下自建（静态、可控），任务完全按真实流程走：
登录 → 读表格挑出最新一张发票 → 打开 → 下载 → 校验内容 → 落盘去重。
"""

import json


def _counted(tools):
    stats = {"calls": 0, "bytes": 0}

    async def call(name, *a, **k):
        res = await tools[name](*a, **k)
        stats["calls"] += 1
        stats["bytes"] += len(json.dumps(res, ensure_ascii=False))
        return res

    return call, stats


async def test_task_supplier_invoice_end_to_end(gavel_tools, webapp_server,
                                                tmp_path):
    """任务：登录供应商后台 → 挑出最新发票 → 下载 → 校验内容 → 落盘去重。"""
    call, _ = _counted(gavel_tools)
    base = webapp_server
    r = await call("session_create")
    pid = r["page_id"]

    # 1. 登录（真实填值 + 点击 + 断言已跳转）
    nav = await call("page_navigate", page_id=pid,
                     url=f"{base}/portal/login.html",
                     assertions={"t": {"read": "title", "op": "contains",
                                       "value": "Login"}})
    assert nav["status"] == "pass", nav
    await call("locator_fill", page_id=pid,
               target={"by": "css", "value": "#user"}, value="acme")
    await call("locator_fill", page_id=pid,
               target={"by": "css", "value": "#pass"}, value="s3cret")
    login = await call("locator_click", page_id=pid,
                       target={"by": "css", "value": "#login-btn"},
                       assertions={"u": {"read": "url", "op": "contains",
                                         "value": "invoices.html"}}, wait_s=5)
    assert login["status"] == "pass", login

    # 2. 读表格，程序挑出日期最新的那张（不写死行号）
    rd = await call("page_read", page_id=pid, page_features={
        "ids": "Array.from(document.querySelectorAll('.inv-id')).map(e=>e.textContent)",
        "dates": "Array.from(document.querySelectorAll('.inv-date')).map(e=>e.textContent)"})
    ids, dates = rd["features"]["ids"], rd["features"]["dates"]
    newest = ids[max(range(len(dates)), key=lambda i: dates[i])]
    assert newest == "A-2026-09-30", rd

    # 3. 打开最新发票（断言页面确实切到了这一张）
    op = await call("locator_click", page_id=pid,
                    target={"by": "css",
                            "value": f'a.open[href="invoice_{newest}.html"]'},
                    assertions={"id": {"selector": "#invoice-id", "op": "eq",
                                       "value": newest}}, wait_s=5)
    assert op["status"] == "pass", op

    # 4. 读金额 + 下载 + 校验下载内容真的对得上
    amt = await call("page_read", page_id=pid, page_features={
        "a": "document.querySelector('#invoice-amount').innerText"})
    assert amt["features"]["a"] == "1234.56", amt
    await call("locator_click", page_id=pid,
               target={"by": "css", "value": "#download"})
    dl = await call("page_wait_for_download", page_id=pid, timeout_s=10)
    assert dl["status"] == "ok" and dl["suggested_filename"].startswith("invoice_A"), dl
    txt = await call("download_read_text", artifact_id=dl["artifact_id"])
    assert "1234.56" in txt["text"] and "2026-09-30" in txt["text"], txt

    # 5. 落盘 + 去重（第二次同名不覆盖，自动改名）
    s1 = await call("download_save", artifact_id=dl["artifact_id"],
                    dest_dir=str(tmp_path))
    assert s1["exported"] and s1["filename"] == "invoice_A-2026-09-30.txt", s1
    assert (tmp_path / "invoice_A-2026-09-30.txt").exists()
    s2 = await call("download_save", artifact_id=dl["artifact_id"],
                    dest_dir=str(tmp_path))
    assert s2["filename"] != s1["filename"], s2  # 没覆盖
    assert len(list(tmp_path.glob("invoice_A-2026-09-30*.txt"))) == 2


async def test_task_shop_search_open_check_price(gavel_tools, webapp_server):
    """任务：搜索商品 → 打开结果 → 读价格并判定落在合理区间。"""
    call, _ = _counted(gavel_tools)
    base = webapp_server
    r = await call("session_create")
    pid = r["page_id"]
    await call("page_navigate", page_id=pid, url=f"{base}/portal/shop.html")

    await call("locator_fill", page_id=pid,
               target={"by": "css", "value": "#q"}, value="widget")
    sr = await call("locator_click", page_id=pid,
                    target={"by": "css", "value": "#search"},
                    assertions={"c": {"read": "text", "selector": "#count",
                                      "op": "contains", "value": "1 results"}},
                    wait_s=5)
    assert sr["status"] == "pass", sr
    op = await call("locator_click", page_id=pid,
                    target={"by": "css", "value": ".result-link"},
                    assertions={"n": {"selector": "#product-name",
                                      "op": "contains", "value": "Widget"}},
                    wait_s=5)
    assert op["status"] == "pass", op
    pr = await call("page_read", page_id=pid, page_features={
        "p": "parseFloat(document.querySelector('#price').innerText)"})
    price = pr["features"]["p"]
    assert 10 <= price <= 100, pr  # 业务规则：价格须在合理区间


async def test_task_missing_element_is_explained(gavel_tools, webapp_server):
    """任务失败可解释：点到不存在的元素要返回结构化错误 + 可恢复提示，而非崩溃。"""
    call, _ = _counted(gavel_tools)
    base = webapp_server
    r = await call("session_create")
    pid = r["page_id"]
    await call("page_navigate", page_id=pid,
               url=f"{base}/portal/invoice_A-2026-09-30.html")
    bad = await call("locator_click", page_id=pid,
                     target={"by": "css", "value": "#no-such-button"})
    assert bad["status"] == "error" and bad["reason"] == "locator_not_found", bad
    assert bad.get("hint")  # 模型能读懂下一步怎么做


async def test_task_repair_after_page_changed(gavel_tools, webapp_server):
    """页面改版（按钮 id 变化）→ 模板定位失败 → 局部修复换锚点 → 通过并发布新版。"""
    T, base = gavel_tools, webapp_server
    r = await T["session_create"]()
    tmpl = {"template_id": "portal_shop_repair", "site": "portal", "desc": "repair",
            "failure_policy": {"retry": "safe_only",
                               "repair": "explore_local_step"},
            "steps": [
                {"id": "nav", "action": {
                    "type": "navigate", "url": f"{base}/portal/shop_v2.html"},
                 "checkpoint": {"type": "title_contains", "value": "Shop"}},
                {"id": "fill", "action": {
                    "type": "fill", "target": {"by": "css", "value": "#q"},
                    "value": "widget"}},
                {"id": "search", "action": {
                    "type": "click", "target": {"by": "css", "value": "#search"}},
                 "checkpoint": {"type": "text_contains", "selector": "#count",
                                "value": "1 results"}}]}
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="portal_shop_repair",
                                  session_id=r["session_id"], verbosity="steps")
    assert res["status"] == "pass", res
    assert any(s.get("repaired") for s in res["steps"]), res
    assert res.get("republished"), res  # 修复后发布新版本
    # 已完成的步骤不重复；修复后重跑应无需再修
    res2 = await T["workflow_run"](name_or_id="portal_shop_repair",
                                   session_id=r["session_id"], verbosity="steps")
    assert res2["status"] == "pass", res2
    assert not any(s.get("repaired") for s in res2["steps"]), res2


async def test_task_replay_cheaper_than_explore(gavel_tools, webapp_server):
    """重放闭环：同一任务，探索跑 vs 模板一次重放，往返次数与回传量显著下降。"""
    base = webapp_server
    explore, estats = _counted(gavel_tools)
    r = await explore("session_create")
    pid = r["page_id"]
    await explore("page_navigate", page_id=pid, url=f"{base}/portal/shop.html")
    await explore("locator_fill", page_id=pid,
                  target={"by": "css", "value": "#q"}, value="widget")
    await explore("locator_click", page_id=pid,
                  target={"by": "css", "value": "#search"},
                  assertions={"c": {"read": "text", "selector": "#count",
                                    "op": "contains", "value": "1 results"}})
    await explore("locator_click", page_id=pid,
                  target={"by": "css", "value": ".result-link"},
                  assertions={"n": {"selector": "#product-name",
                                    "op": "contains", "value": "Widget"}})

    tmpl = {"template_id": "portal_shop_search", "site": "portal",
            "desc": "shop search",
            "steps": [
                {"id": "nav", "action": {"type": "navigate",
                                         "url": f"{base}/portal/shop.html"},
                 "checkpoint": {"type": "title_contains", "value": "Shop"}},
                {"id": "fill", "action": {"type": "fill",
                                          "target": {"by": "css", "value": "#q"},
                                          "value": "$QUERY"}},
                {"id": "search", "action": {"type": "click",
                                            "target": {"by": "css", "value": "#search"}},
                 "checkpoint": {"type": "text_contains", "selector": "#count",
                                "value": "1 results"}},
                {"id": "open", "action": {"type": "click",
                                          "target": {"by": "css",
                                                     "value": ".result-link"}},
                 "checkpoint": {"type": "text_contains",
                                "selector": "#product-name", "value": "Widget"}}]}
    await gavel_tools["workflow_save"](template=tmpl)

    replay, rstats = _counted(gavel_tools)
    res = await replay("workflow_run", name_or_id="portal_shop_search",
                       params={"QUERY": "widget"})
    assert res["status"] == "pass", res
    # 一次重放调用 vs 探索的多次调用；回传量也明显更小
    assert rstats["calls"] == 1 and estats["calls"] >= 4
    assert rstats["bytes"] < estats["bytes"], (rstats, estats)
    print(f"\n[replay-vs-explore] explore calls={estats['calls']} "
          f"bytes={estats['bytes']} | replay calls={rstats['calls']} "
          f"bytes={rstats['bytes']}")
