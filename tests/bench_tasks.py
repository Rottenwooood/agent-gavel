"""真实任务级基准（不是单操作/生命周期）。

跑的是多步业务任务，既用自建本地站点（可控、可回归），也跑真实网站（联网）：

  本地 portal：登录后台 → 挑最新发票 → 下载 → 校验内容 → 落盘去重
  本地 shop  ：搜索 → 打开结果 → 读价格并判定区间
  本地 重放  ：同一任务，模板一次重放 vs 探索，比往返次数与回传字节
  真实 example.com：导航 → 读标题/h1 → 断言
  真实 wikipedia  ：搜索 → 回车 → 断言跳转 + 读结果页

指标：成功与否、工具调用次数、耗时、回传字节（token 的代理，不是真实 token）。
真实站点可跳过：AGENT_GAVEL_BENCH_OFFLINE=1 uv run python tests/bench_tasks.py

用法：AGENT_GAVEL_HEADLESS=1 uv run python tests/bench_tasks.py
产出：终端表格 + logs/tasks-<ts>.json
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp_server import start_server  # noqa: E402

from agent_gavel.runtime import reset_runtime, shutdown_runtime  # noqa: E402
from agent_gavel.tools import register_runtime_tools  # noqa: E402

OFFLINE = os.environ.get("AGENT_GAVEL_BENCH_OFFLINE") == "1"
PROXY = (os.environ.get("AGENT_GAVEL_BENCH_PROXY")
         or os.environ.get("https_proxy") or os.environ.get("http_proxy"))


def _proxy_arg():
    return {"server": PROXY} if PROXY else None


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class Caller:
    """包一层统计：调用次数 + 回传字节（token 代理）。"""

    def __init__(self, tools):
        self.tools = tools
        self.calls = 0
        self.bytes = 0

    async def __call__(self, name, **kw):
        res = await self.tools[name](**kw)
        self.calls += 1
        self.bytes += len(json.dumps(res, ensure_ascii=False))
        return res

    def reset(self):
        self.calls = 0
        self.bytes = 0


RESULTS = []


def record(name, ok, detail, c: Caller, t0):
    RESULTS.append({"task": name, "ok": bool(ok),
                    "calls": c.calls, "bytes": c.bytes,
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}  calls={c.calls} bytes={c.bytes} "
          f"{round((time.perf_counter() - t0) * 1000, 1)}ms  {detail}")


async def task_invoice(gavel, base, tmpdir):
    c = Caller(gavel)
    t0 = time.perf_counter()
    r = await c("session_create")
    pid = r["page_id"]
    nav = await c("page_navigate", page_id=pid, url=f"{base}/portal/login.html",
                  assertions={"t": {"read": "title", "op": "contains",
                                    "value": "Login"}})
    await c("locator_fill", page_id=pid, target={"by": "css", "value": "#user"},
            value="acme")
    await c("locator_fill", page_id=pid, target={"by": "css", "value": "#pass"},
            value="s3cret")
    login = await c("locator_click", page_id=pid,
                    target={"by": "css", "value": "#login-btn"},
                    assertions={"u": {"read": "url", "op": "contains",
                                      "value": "invoices.html"}}, wait_s=5)
    rd = await c("page_read", page_id=pid, page_features={
        "ids": "Array.from(document.querySelectorAll('.inv-id')).map(e=>e.textContent)",
        "dates": "Array.from(document.querySelectorAll('.inv-date')).map(e=>e.textContent)"})
    ids, dates = rd["features"]["ids"], rd["features"]["dates"]
    newest = ids[max(range(len(dates)), key=lambda i: dates[i])]
    op = await c("locator_click", page_id=pid,
                 target={"by": "css", "value": f'a.open[href="invoice_{newest}.html"]'},
                 assertions={"id": {"selector": "#invoice-id", "op": "eq",
                                    "value": newest}}, wait_s=5)
    await c("locator_click", page_id=pid, target={"by": "css", "value": "#download"})
    dl = await c("page_wait_for_download", page_id=pid, timeout_s=10)
    txt = await c("download_read_text", artifact_id=dl["artifact_id"])
    ok = (nav["status"] == "pass" and login["status"] == "pass"
          and op["status"] == "pass" and dl["status"] == "ok"
          and "1234.56" in txt["text"] and "2026-09-30" in txt["text"])
    record("本地:登录→选最新发票→下载→校验", ok, f"invoice={newest}", c, t0)


async def task_shop(gavel, base):
    c = Caller(gavel)
    t0 = time.perf_counter()
    r = await c("session_create")
    pid = r["page_id"]
    await c("page_navigate", page_id=pid, url=f"{base}/portal/shop.html")
    await c("locator_fill", page_id=pid, target={"by": "css", "value": "#q"},
            value="widget")
    sr = await c("locator_click", page_id=pid,
                 target={"by": "css", "value": "#search"},
                 assertions={"c": {"read": "text", "selector": "#count",
                                   "op": "contains", "value": "1 results"}})
    op = await c("locator_click", page_id=pid,
                 target={"by": "css", "value": ".result-link"},
                 assertions={"n": {"selector": "#product-name", "op": "contains",
                                   "value": "Widget"}})
    pr = await c("page_read", page_id=pid, page_features={
        "p": "parseFloat(document.querySelector('#price').innerText)"})
    price = pr["features"]["p"]
    ok = (sr["status"] == "pass" and op["status"] == "pass"
          and 10 <= price <= 100)
    record("本地:搜索→打开→价格区间", ok, f"price={price}", c, t0)


async def task_replay(gavel, base):
    c = Caller(gavel)
    t0 = time.perf_counter()
    r = await c("session_create")
    pid = r["page_id"]
    await c("page_navigate", page_id=pid, url=f"{base}/portal/shop.html")
    await c("locator_fill", page_id=pid, target={"by": "css", "value": "#q"},
            value="widget")
    await c("locator_click", page_id=pid, target={"by": "css", "value": "#search"},
            assertions={"c": {"read": "text", "selector": "#count",
                              "op": "contains", "value": "1 results"}})
    await c("locator_click", page_id=pid, target={"by": "css", "value": ".result-link"},
            assertions={"n": {"selector": "#product-name", "op": "contains",
                              "value": "Widget"}})
    explore_calls, explore_bytes = c.calls, c.bytes

    await gavel["workflow_save"](template={
        "template_id": "bench_shop_search", "site": "portal", "desc": "bench",
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
                                      "target": {"by": "css", "value": ".result-link"}},
             "checkpoint": {"type": "text_contains", "selector": "#product-name",
                            "value": "Widget"}}]})
    rc = Caller(gavel)
    res = await rc("workflow_run", name_or_id="bench_shop_search",
                   session_id=r["session_id"], params={"QUERY": "widget"})
    ok = res["status"] == "pass" and explore_calls >= 4 and rc.calls == 1
    detail = (f"explore={explore_calls}call/{explore_bytes}B -> "
              f"replay={rc.calls}call/{rc.bytes}B")
    RESULTS.append({"task": "本地:重放 vs 探索", "ok": bool(ok),
                    "calls": rc.calls, "bytes": rc.bytes,
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] 本地:重放 vs 探索  {detail}")


async def task_real_example(gavel):
    c = Caller(gavel)
    t0 = time.perf_counter()
    r = await c("session_create", proxy=_proxy_arg())
    pid = r["page_id"]
    nav = await c("page_navigate", page_id=pid, url="https://example.com",
                  assertions={"t": {"read": "title", "op": "contains",
                                    "value": "Example"}})
    rd = await c("page_read", page_id=pid, page_features={
        "body": "document.body ? document.body.innerText.slice(0,200) : null"})
    body = rd["features"]["body"] or ""
    ok = nav["status"] == "pass" and "documentation" in body.lower()
    record("真实:example.com 导航+读正文", ok, f"body={body[:48]!r}", c, t0)


async def task_real_wikipedia(gavel):
    c = Caller(gavel)
    t0 = time.perf_counter()
    r = await c("session_create", proxy=_proxy_arg())
    pid = r["page_id"]
    nav = await c("page_navigate", page_id=pid,
                  url="https://en.wikipedia.org/wiki/Main_Page", timeout_s=40)
    if nav["status"] != "ok":
        record("真实:wikipedia 搜索→结果页", False,
               f"navigate={nav.get('reason') or nav.get('error')}", c, t0)
        return
    await c("page_wait_for_selector", page_id=pid, selector="#searchInput",
            timeout_s=20)
    await c("locator_fill", page_id=pid,
            target={"by": "css", "value": "#searchInput"}, value="Playwright")
    ent = await c("locator_press", page_id=pid,
                  target={"by": "css", "value": "#searchInput"}, keys="Enter",
                  wait_navigation=True,
                  assertions={"u": {"read": "url", "op": "contains",
                                    "value": "Playwright"}}, wait_s=20)
    rd = await c("page_read", page_id=pid, page_features={
        "t": "document.title"})
    ok = ent["status"] == "pass" and "Playwright" in (rd["features"]["t"] or "")
    record("真实:wikipedia 搜索→结果页", ok, f"title={rd['features']['t']!r}", c, t0)


async def main():
    httpd, base = start_server()
    rt = await reset_runtime()
    mcp = FakeMCP()
    register_runtime_tools(mcp)
    gavel = mcp.tools
    try:
        print(f"base={base}  offline={OFFLINE}")
        await task_invoice(gavel, base, None)
        await task_shop(gavel, base)
        await task_replay(gavel, base)
        if not OFFLINE:
            await task_real_example(gavel)
            await task_real_wikipedia(gavel)
    finally:
        await shutdown_runtime()
        httpd.shutdown()

    total = len(RESULTS)
    ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n=== 任务 {ok}/{total} 通过 ===")
    os.makedirs("logs", exist_ok=True)
    out = os.path.join("logs", f"tasks-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"报告已写入 {out}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
