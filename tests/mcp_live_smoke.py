"""真实 MCP 协议联调（不是单元测试）。

以 MCP 客户端身份用 stdio 拉起源码 server（agent_gavel.main），走
initialize → tools/list → tools/call，对真实网站执行一次完整流程：

  1. 列出工具，确认新 runtime 工具已注册
  2. session_create → page_navigate(example.com) → 读标题 → explore → 截图
  3. 百度搜索（真实交互：填词 → Enter → 断言结果页）→ 读结果标题
  4. 收尾关闭，确认无残留

用法：uv run python tests/mcp_live_smoke.py
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}  {detail}")


async def call(session, name, args=None):
    res = await session.call_tool(name, args or {})
    if res.is_error:
        return {"_isError": True,
                "_text": "\n".join(getattr(c, "text", "") for c in res.content)}
    sc = getattr(res, "structured_content", None)
    if sc is not None:
        return sc
    texts = [getattr(c, "text", "") for c in res.content]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except Exception:
        return {"_text": joined}


async def main():
    import tempfile
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_gavel.main"],
        env={**os.environ, "AGENT_GAVEL_HEADLESS": "1",
             "AGENT_GAVEL_WORKFLOWS_DIR":
                 tempfile.mkdtemp(prefix="ag-live-workflows-")},
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = {t.name for t in tools.tools}
            new = sorted(n for n in names if n.split("_")[0] in
                         ("browser", "session", "context", "page", "locator",
                          "expect"))
            print(f"tools/list: 共 {len(names)} 个；新 runtime 工具 {len(new)} 个")
            check("新工具已注册 (page_navigate/locator_fill/expect)",
                  {"page_navigate", "locator_fill", "expect"} <= names)

            # 1. example.com
            st = await call(s, "browser_status")
            check("browser_status", st.get("status") == "ok")
            sc = await call(s, "session_create", {})
            pid = sc.get("page_id")
            check("session_create", sc.get("status") == "ok" and bool(pid),
                  f"page_id={pid}")

            nav = await call(s, "page_navigate",
                             {"page_id": pid, "url": "https://example.com"})
            check("page_navigate example.com",
                  nav.get("status") == "ok" and "example.com" in (nav.get("final_url") or ""),
                  f"title={nav.get('title')!r} url={nav.get('final_url')}")

            rd = await call(s, "page_read", {"page_id": pid, "page_features": {
                "title": "document.title",
                "h1": "document.querySelector('h1') ? document.querySelector('h1').innerText : null"}})
            check("page_read", rd.get("status") == "ok",
                  f"features={rd.get('features')}")

            ex = await call(s, "page_explore", {"page_id": pid})
            check("page_explore", ex.get("status") == "ok",
                  f"total={ex.get('total')}")

            ss = await call(s, "page_screenshot", {"page_id": pid})
            check("page_screenshot", ss.get("status") == "ok" and ss.get("width", 0) > 0,
                  f"artifact={ss.get('artifact_id')} {ss.get('width')}x{ss.get('height')}")

            txt = await call(s, "page_text", {"page_id": pid, "mode": "text"})
            check("page_text 非空", bool((txt.get("text") or "").strip()))

            # 诊断 + 工作流（M4，真实 MCP）
            pm = await call(s, "performance_metrics")
            check("performance_metrics", pm.get("status") == "ok"
                  and bool(pm.get("metrics")))
            bad = await call(s, "workflow_validate",
                             {"template": {"template_id": "x", "steps": []}})
            check("workflow_validate 拒绝非法模板", bad.get("valid") is False)
            saved = await call(s, "workflow_save", {"template": {
                "template_id": "live_example", "site": "example.com",
                "desc": "live",
                "steps": [{"id": "s1", "action": {"type": "navigate",
                                                  "url": "https://example.com"},
                           "checkpoint": {"type": "url_contains",
                                          "value": "example.com"}}]}})
            check("workflow_save", saved.get("status") == "ok",
                  f"version={saved.get('version')}")
            wrun = await call(s, "workflow_run",
                              {"name_or_id": "live_example"})
            check("workflow_run 单调用重放", wrun.get("status") == "pass",
                  f"checkpoints={wrun.get('checkpoints_passed')}")
            wl = await call(s, "workflow_list")
            check("workflow_list", any(
                w.get("template_id") == "live_example"
                for w in wl.get("items", [])))

            # 2. 真实站点交互 + 断言：Wikipedia 搜索
            await call(s, "session_close", {"session_id": sc.get("session_id")})
            sc2 = await call(s, "session_create", {})
            pid2 = sc2.get("page_id")
            nav2 = await call(s, "page_navigate", {
                "page_id": pid2,
                "url": "https://en.wikipedia.org/wiki/Main_Page"})
            check("page_navigate wikipedia", nav2.get("status") == "ok",
                  f"title={nav2.get('title')!r}")
            filt = await call(s, "locator_fill", {
                "page_id": pid2,
                "target": {"by": "css", "value": "#searchInput"},
                "value": "Playwright"})
            check("填搜索框", filt.get("status") in ("ok", "pass"),
                  f"detail={filt.get('reason') or filt.get('status')}")
            ent = await call(s, "locator_press", {
                "page_id": pid2,
                "target": {"by": "css", "value": "#searchInput"},
                "keys": "Enter", "wait_navigation": True,
                "assertions": {"u": {"read": "url", "op": "contains",
                                     "value": "Playwright"}},
                "wait_s": 10.0})
            check("回车并断言跳转到搜索结果",
                  ent.get("status") == "pass",
                  f"status={ent.get('status')} url={ent.get('execution',{}).get('url_after')}")
            results = await call(s, "page_read", {"page_id": pid2, "page_features": {
                "title": "document.title",
                "n": "document.querySelectorAll('a[href^=\"/wiki/\"]').length"}})
            check("结果页可读", results.get("status") == "ok",
                  f"{results.get('features')}")

            # 3. 收尾
            await call(s, "session_close", {"session_id": sc2.get("session_id")})
            bc = await call(s, "browser_close")
            check("browser_close", bc.get("status") == "ok")

    total = len(_results)
    ok = sum(_results)
    print(f"\n=== {ok}/{total} 通过 ===")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
