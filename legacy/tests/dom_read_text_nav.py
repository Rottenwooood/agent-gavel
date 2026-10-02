"""DOM 新增能力端到端测试：dom_read / dom_text / 函数形式 / 导航等待 / navigate(url only)。

通过 MCP stdio 会话真实调用工具（端到端，非直连内部函数）。用法:
  cd agent-gavel && uv run python3 tests/dom_read_text_nav.py

覆盖:
  1 dom_navigate 只给 url（site 可选）
  2 dom_text 整页取文字（含换行）
  3 dom_text(selector) 取元素文字
  4 dom_read 只读取值（不判定/不重试）
  5 dom_step page_features 函数形式（多语句/赋值）
  6 dom_read 函数形式
  7 dom_step click + wait_navigation -> 返回跳转后 url/title
  8 dom_step click + 无断言 -> diff 兜底（回归）
"""

import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = f"file://{os.path.join(HERE, 'fixtures', 'read_text_nav.html')}"
FIX2 = f"file://{os.path.join(HERE, 'fixtures', 'page2.html')}"
ROOT = os.path.dirname(HERE)


def parse(res):
    for c in res.content:
        if getattr(c, "type", "") == "text":
            try:
                return json.loads(c.text)
            except Exception:
                return c.text
    return None


async def call(s, name, args, timeout=40):
    t0 = time.perf_counter()
    r = await s.call_tool(name, args)
    ms = int((time.perf_counter() - t0) * 1000)
    return parse(r), ms


async def main():
    ok_all = True
    params = StdioServerParameters(command=os.path.join(ROOT, "run-mcp.sh"), args=[])
    params.env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
    }
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("新工具已注册:", [t for t in tools if t in
                                    ("dom_read", "dom_text")])

            def check(name, cond, info):
                nonlocal ok_all
                if not cond:
                    ok_all = False
                print(f"[{'OK ' if cond else 'BAD'}] {name:<40} {info}")

            # 1 navigate 只给 url
            r, ms = await call(s, "dom_navigate", {"url": FIX})
            check("1 navigate(url only)", r and r.get("status") == "pass"
                  and "read_text_nav" in (r.get("url") or ""),
                  f"{ms}ms url={r.get('url') if isinstance(r,dict) else r}")

            # 2 dom_text 整页（含换行）
            r, ms = await call(s, "dom_text", {})
            txt = r.get("text", "") if isinstance(r, dict) else ""
            check("2 dom_text 整页含换行", r.get("status") == "ok"
                  and "文本内容 ABC" in txt and "\n" in txt,
                  f"{ms}ms len={r.get('length')}")

            # 3 dom_text selector
            r, ms = await call(s, "dom_text", {"selector": "#out"})
            check("3 dom_text(selector)", r.get("status") == "ok"
                  and r.get("text") == "init", f"{ms}ms text={r.get('text')!r}")

            # 4 dom_read 只读取值
            r, ms = await call(s, "dom_read", {"page_features": {
                "t": "document.title",
                "o": "document.querySelector('#out').textContent"}})
            f = r.get("features", {}) if isinstance(r, dict) else {}
            check("4 dom_read 取值", r.get("status") == "ok"
                  and f.get("t") == "read-text-nav" and f.get("o") == "init",
                  f"{ms}ms features={f}")

            # 5 dom_step 函数形式 page_features（多语句 + 赋值）
            r, ms = await call(s, "dom_step", {
                "action": "click", "selectors": {"target": "#btn"},
                "page_features": {"x": "() => { const v = document.querySelector('#out').textContent; return v + '-fn'; }"},
                "expected_feature": {"x": {"op": "eq", "value": "clicked-fn"}}})
            check("5 dom_step 函数形式", r.get("status") == "pass",
                  f"{ms}ms status={r.get('status')} ev={r.get('evidence')}")

            # 6 dom_read 函数形式
            r, ms = await call(s, "dom_read", {"page_features": {
                "y": "() => { const a = document.title; return a + '!'; }"}})
            check("6 dom_read 函数形式", r.get("features", {}).get("y") == "read-text-nav!",
                  f"{ms}ms features={r.get('features')}")

            # 重置页面
            await call(s, "dom_navigate", {"url": FIX})

            # 7 click + wait_navigation -> 跳转后 url
            r, ms = await call(s, "dom_step", {
                "action": "click", "selectors": {"target": "#nav"},
                "wait_navigation": True, "strict": True})
            url = r.get("verification", {}).get("url") or r.get("url")
            check("7 click+wait_navigation", "page2" in (url or ""),
                  f"{ms}ms url={url}")

            # 8 回归：click 无断言 -> diff 兜底
            await call(s, "dom_navigate", {"url": FIX})
            r, ms = await call(s, "dom_step", {
                "action": "click", "selectors": {"target": "#btn"}, "strict": True})
            check("8 click 无断言 diff 兜底", r.get("status") == "pass",
                  f"{ms}ms status={r.get('status')} mode={r.get('verification',{}).get('mode')}")

    print("\n" + ("ALL PASS" if ok_all else "HAS FAIL"))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    asyncio.run(main())
