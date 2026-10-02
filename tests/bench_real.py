"""真实网站任务基准（重头戏）。

不是 example.com 这种"能打开就行"，而是**真实站点上的多步任务**：进入站点 →
搜索/导航 → 抽取业务数据 → 用程序断言数据正确（标题匹配、价格为数值/区间、
首句含关键词等）。另含一个真实任务上的"探索 vs 模板重放"对比。

站点：Hacker News / Wikipedia / Books to Scrape / Quotes to Scrape。
HTTP(S) 代理取自 http(s)_proxy（这些站在本机直连多不可达）。

用法：AGENT_GAVEL_HEADLESS=1 uv run python tests/bench_real.py
产出：终端表格 + logs/real-<ts>.json
"""

import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_gavel.runtime import reset_runtime, shutdown_runtime  # noqa: E402
from agent_gavel.tools import register_runtime_tools  # noqa: E402

PROXY = (os.environ.get("AGENT_GAVEL_BENCH_PROXY")
         or os.environ.get("https_proxy") or os.environ.get("http_proxy"))
PROXY_ARG = {"server": PROXY} if PROXY else None


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class Caller:
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


def record(task, ok, c, t0, detail):
    RESULTS.append({"task": task, "ok": bool(ok), "calls": c.calls,
                    "bytes": c.bytes,
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {task}  calls={c.calls} "
          f"bytes={c.bytes} {RESULTS[-1]['wall_ms']}ms  {detail}")


async def _new_session(c, **kw):
    r = await c("session_create", proxy=PROXY_ARG, **kw)
    return r["page_id"]


# ---------------- 真实任务 ----------------

async def task_hackernews(gavel):
    """Hacker News：抽取首页榜单（前 10 条标题 + 分数），校验标题互异、分数为整数。"""
    c = Caller(gavel)
    t0 = time.perf_counter()
    pid = await _new_session(c)
    nav = await c("page_navigate", page_id=pid,
                  url="https://news.ycombinator.com/", timeout_s=40,
                  assertions={"t": {"read": "title", "op": "contains",
                                    "value": "Hacker News"}})
    rd = await c("page_read", page_id=pid, page_features={
        "titles": ("Array.from(document.querySelectorAll("
                   "'tr.athing .titleline a')).slice(0,10).map(a=>a.innerText)"),
        "scores": ("Array.from(document.querySelectorAll('.score'))"
                   ".slice(0,10).map(s=>parseInt(s.innerText,10))")})
    titles = (rd.get("features") or {}).get("titles") or []
    scores = (rd.get("features") or {}).get("scores") or []
    ok = (nav["status"] == "pass" and len(titles) >= 5
          and len(set(titles)) == len(titles)
          and len(scores) >= 3
          and all(isinstance(s, int) and s > 0 for s in scores))
    record("真实:HN 首页榜单抽取→标题互异/分数为整数", ok, c, t0,
           f"n={len(titles)} top={titles[0][:30]!r} score0={scores[0] if scores else None}")


async def task_wikipedia(gavel):
    """Wikipedia：搜索 → 打开词条 → 抽取首段首句并断言含关键词。"""
    c = Caller(gavel)
    t0 = time.perf_counter()
    pid = await _new_session(c)
    nav = await c("page_navigate", page_id=pid, timeout_s=40,
                  url="https://en.wikipedia.org/wiki/Main_Page")
    await c("page_wait_for_selector", page_id=pid, selector="#searchInput",
            timeout_s=20)
    await c("locator_fill", page_id=pid,
            target={"by": "css", "value": "#searchInput"}, value="Web scraping")
    ent = await c("locator_press", page_id=pid,
                  target={"by": "css", "value": "#searchInput"}, keys="Enter",
                  wait_navigation=True, wait_s=20,
                  assertions={"u": {"read": "url", "op": "contains",
                                    "value": "Web_scraping"}})
    rd = await c("page_read", page_id=pid, page_features={
        "h": "document.querySelector('#firstHeading').innerText",
        "p": "(document.querySelector('#mw-content-text p')||{}).innerText||''"})
    h = rd["features"]["h"] or ""
    p = rd["features"]["p"] or ""
    ok = (nav["status"] == "ok" and ent["status"] == "pass"
          and "Web scraping" in h and "web" in p.lower())
    record("真实:Wikipedia 搜索→词条→首段校验", ok, c, t0,
           f"heading={h!r} p1={p[:40]!r}")


async def task_books(gavel):
    """Books to Scrape：进入 Travel 分类 → 取第一本书标题/价格 → 价格区间校验。"""
    c = Caller(gavel)
    t0 = time.perf_counter()
    pid = await _new_session(c)
    nav = await c("page_navigate", page_id=pid, timeout_s=40,
                  url="https://books.toscrape.com/",
                  assertions={"t": {"read": "title", "op": "contains",
                                    "value": "Books"}})
    clk = await c("locator_click", page_id=pid,
                  target={"by": "text", "value": "Travel"}, wait_s=8,
                  assertions={"u": {"read": "url", "op": "contains",
                                    "value": "travel"}})
    rd = await c("page_read", page_id=pid, page_features={
        "title": "document.querySelector('.product_pod h3 a').getAttribute('title')",
        "price": "document.querySelector('.product_pod .price_color').innerText"})
    title = rd["features"]["title"]
    price_s = rd["features"]["price"] or ""
    m = re.search(r"[\d.]+", price_s.replace("£", ""))
    price = float(m.group()) if m else None
    ok = (nav["status"] == "pass" and clk["status"] == "pass" and bool(title)
          and price is not None and 0 < price < 100)
    record("真实:Books 分类→取书标题/价格→区间校验", ok, c, t0,
           f"title={title!r} price={price_s!r}")


async def task_quotes(gavel):
    """Quotes to Scrape：进入 love 标签 → 取首条名言与作者 → 非空校验。"""
    c = Caller(gavel)
    t0 = time.perf_counter()
    pid = await _new_session(c)
    nav = await c("page_navigate", page_id=pid, timeout_s=40,
                  url="https://quotes.toscrape.com/tag/love/",
                  assertions={"t": {"read": "title", "op": "contains",
                                    "value": "Quotes"}})
    rd = await c("page_read", page_id=pid, page_features={
        "q": "document.querySelector('.quote .text').innerText",
        "a": "document.querySelector('.quote .author').innerText"})
    q = rd["features"]["q"] or ""
    a = rd["features"]["a"] or ""
    ok = nav["status"] == "pass" and len(q) > 10 and bool(a)
    record("真实:Quotes 标签页→取名言/作者→非空校验", ok, c, t0,
           f"author={a!r} q={q[:34]!r}")


async def task_real_replay(gavel):
    """真实任务上的探索 vs 模板重放：Wikipedia 搜索，比往返与回传量。"""
    t0 = time.perf_counter()
    # 探索
    ex = Caller(gavel)
    r0 = await ex("session_create", proxy=PROXY_ARG)
    sid, pid = r0["session_id"], r0["page_id"]
    await ex("page_navigate", page_id=pid, timeout_s=40,
             url="https://en.wikipedia.org/wiki/Main_Page")
    await ex("page_wait_for_selector", page_id=pid, selector="#searchInput",
             timeout_s=20)
    await ex("locator_fill", page_id=pid,
             target={"by": "css", "value": "#searchInput"}, value="Playwright")
    ent = await ex("locator_press", page_id=pid,
                   target={"by": "css", "value": "#searchInput"}, keys="Enter",
                   wait_navigation=True, wait_s=20)
    explore_ok = ent["status"] in ("ok", "pass")
    explore_calls, explore_bytes = ex.calls, ex.bytes

    await gavel["workflow_save"](template={
        "template_id": "real_wikipedia_search", "site": "wikipedia",
        "desc": "Wikipedia search", "task": "搜一个词并打开词条",
        "login_required": False, "status": "verified",
        "parameters": {"QUERY": {"type": "string", "sensitive": False}},
        "steps": [
            {"id": "nav", "action": {"type": "navigate", "timeout_s": 40,
                                     "url": "https://en.wikipedia.org/wiki/Main_Page"},
             "checkpoint": {"type": "title_contains", "value": "Wikipedia"}},
            {"id": "fill", "action": {"type": "fill",
                                      "target": {"by": "css", "value": "#searchInput"},
                                      "value": "$QUERY"}},
            {"id": "press", "action": {"type": "press",
                                       "target": {"by": "css", "value": "#searchInput"},
                                       "keys": "Enter", "wait_navigation": True},
             "checkpoint": {"type": "url_contains", "value": "$QUERY"}}]})
    rc = Caller(gavel)
    res = await rc("workflow_run", name_or_id="real_wikipedia_search",
                   session_id=sid, params={"QUERY": "Web_scraping"})
    ok = explore_ok and res["status"] == "pass"
    detail = (f"explore={explore_calls}call/{explore_bytes}B -> "
              f"replay={rc.calls}call/{rc.bytes}B "
              f"replay_status={res.get('status')} reason={res.get('reason')}")
    RESULTS.append({"task": "真实:探索 vs 重放(Wikipedia)",
                    "ok": bool(ok), "calls": rc.calls, "bytes": rc.bytes,
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] 真实:探索 vs 重放(Wikipedia)  {detail}")


async def _run(fn, *a):
    try:
        await fn(*a)
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"task": fn.__name__, "ok": False, "calls": 0,
                        "bytes": 0, "wall_ms": 0,
                        "detail": f"error: {type(e).__name__}: {e}"})
        print(f"  [FAIL] {fn.__name__}  error: {type(e).__name__}: {e}")


async def main():
    rt = await reset_runtime()
    mcp = FakeMCP()
    register_runtime_tools(mcp)
    gavel = mcp.tools
    print(f"proxy={PROXY}")
    try:
        await _run(task_hackernews, gavel)
        await _run(task_wikipedia, gavel)
        await _run(task_books, gavel)
        await _run(task_quotes, gavel)
        await _run(task_real_replay, gavel)
    finally:
        await shutdown_runtime()

    total, ok = len(RESULTS), sum(1 for r in RESULTS if r["ok"])
    print(f"\n=== 真实任务 {ok}/{total} 通过 ===")
    os.makedirs("logs", exist_ok=True)
    out = os.path.join("logs", f"real-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"报告已写入 {out}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
