"""agent-gavel DOM 通道冒烟测试脚本（直连，不经过 MCP 客户端）。

用法: cd agent-gavel && uv run python3 tests/dom_smoke.py
覆盖: 环境自检 → 导航 → 单步断言 → 中文输入 → 完整模板 → 失效记录 → 失败诊断
全过打印 ALL PASS。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gavel.channels.dom.adapter import DomClient
from agent_gavel.channels.dom.verify import dom_act_and_verify


def ok(name, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name} {extra}")
    if not cond:
        raise AssertionError(f"{name} failed {extra}")


async def _step(name):
    print(f"\n== {name} ==", flush=True)


async def main():
    # ---- 1. 环境自检：Chrome 自动拉起 ----
    await _step("1. 环境自检: DomClient 连接(自动拉起可见 Chrome)")
    async with DomClient() as client:
        info = await client.eval_js("navigator.userAgent")
        print(f"  Chrome UA: {info[:80]}...")

        # ---- 2. 导航 ----
        await _step("2. 导航到必应")
        await client.navigate("https://cn.bing.com")
        await client.wait_page_load()
        title = await client.get_title()
        ok("必应标题", "必应" in title, f"got={title!r}")

        # ---- 3. 单步 set_value + 断言 ----
        await _step("3. set_value 填搜索框 + 断言")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#sb_form_q", "value": "opencode"},
            page_features={"v": "document.querySelector('#sb_form_q').value"},
            expected_feature={"v": {"op": "eq", "value": "opencode"}},
            wait_s=4)
        ok("填词断言 pass", r["status"] == "pass", f"status={r['status']}")
        print(f"    策略: {r.get('strategy')} 耗时: {r.get('cost', {}).get('elapsed_ms')}ms")

        # ---- 4. 中文输入 ----
        await _step("4. 中文输入(流浪地球)")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#sb_form_q", "value": "流浪地球"},
            page_features={"v": "document.querySelector('#sb_form_q').value"},
            expected_feature={"v": {"op": "eq", "value": "流浪地球"}},
            wait_s=4)
        ok("中文断言 pass", r["status"] == "pass", f"status={r['status']}")

        # ---- 5. 提交 + 整页跳转断言 ----
        await _step("5. 回车提交, 断言跳转结果页")
        r = await dom_act_and_verify(
            client, action="press_enter",
            page_features={"title": "document.title"},
            expected_feature={"title": {"op": "contains", "value": "流浪地球"}},
            wait_s=8)
        ok("提交跳转 pass", r["status"] == "pass",
           f"status={r['status']} title={(r.get('verification') or {}).get('title','')[:30]}")

        # ---- 6. 失败诊断：错误选择器 ----
        await _step("6. 失败诊断: 错误选择器(应带 reason)")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#definitely-not-exists", "value": "x"},
            page_features={"v": "1"},
            expected_feature={"v": {"op": "eq", "value": "2"}},
            wait_s=1, strict=True)
        ok("错误选择器返回 fail", r["status"] == "fail")
        ok("带 reason", r.get("reason") in ("action_failed", "assertion_timeout"),
           f"reason={r.get('reason')}")

    # ---- 7. 完整模板(自动重拉 Chrome 走新会话) ----
    await _step("7. dom_run_template bing_search")
    from agent_gavel.channels.dom.server import register_dom_tools
    from agent_gavel.channels.dom.templates import resolve_template, _fill
    from agent_gavel.channels.dom.verify import dom_act_and_verify as _dav
    from agent_gavel.channels.dom.templates import record_run

    f, tmpl = resolve_template("bing_search")
    ok("模板可解析", tmpl is not None, f"file={f}")
    filled = _fill(tmpl, {"QUERY": "agent-gavel"})
    async with DomClient() as client:
        for i, step in enumerate(filled["steps"]):
            r = await _dav(client, action=step["action"],
                           selectors=step.get("selectors"),
                           page_features=step.get("page_features"),
                           expected_feature=step.get("expected_feature"),
                           wait_s=8, log_prefix=f"smoke_s{i}")
            ok(f"模板 step{i} pass", r["status"] == "pass", f"status={r['status']}")
        record_run(f, "pass")

    print("\nALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
