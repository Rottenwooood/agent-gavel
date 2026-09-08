"""DOM 通道的 MCP 工具（与 AT-SPI 桌面工具分离，server.py 只留桌面）。

T2 拆分：DOM 网页操作相关工具全部集中于此，通过 register_dom_tools(mcp)
注入同一个 mcp server。server.py 保留桌面(AT-SPI)工具。
"""

import asyncio

from dom_adapter import DomClient
from dom_verify import dom_act_and_verify, _write_log


def register_dom_tools(mcp):
    """把 DOM 通道全部 MCP 工具注册到给定 mcp server。"""

    @mcp.tool()
    async def chrome(action: str = "ensure"):
        """管理 agent-gavel 的调试用 Chrome（T1 自管，无需手动开）。

        action:
          ensure  -> 确保在跑（探测；无则用独立 profile 自启；死了自动重拉）
          stop    -> 停掉 agent-gavel 自己拉起的 Chrome（绝不碰外部手动开的）
          status  -> 只读探测当前状态
        """
        import browser_manager
        if action == "stop":
            return browser_manager.stop_own()
        if action == "status":
            return browser_manager.status()
        return browser_manager.ensure_chrome()

    @mcp.tool()
    async def dom_step(
        action: str,
        selectors: dict = None,
        page_features: dict = None,
        expected_feature: dict = None,
        *,
        debug: int = 0,
        wait_s: float = 6.0,
        trusted: bool = True,
        wait_mode: str = "poll",
    ):
        """通用 DOM 单步闭环：对任意选择器执行一个动作并验证，不绑任何站点。

        这是 AI 现场试错/建流程的核心原语。action 是通用动作：
          navigate  -> selectors.url 导航
          set_value -> selectors.input + selectors.value 设值
          click     -> selectors.target 点击（可用 __text__: / __text_nth__:N:: 锚点）
          press_enter -> 当前焦点触发回车/form 提交
          clear     -> selectors.input 清空（trusted=True 时用真实 Ctrl+A+Backspace，
                       对 React 受控组件有效）
          focus     -> selectors.input 仅聚焦（不清空已填内容）
        page_features: {特征名: JS表达式}，动作后提取页面状态
        expected_feature: {特征名: {op: eq|neq|exists|not_exists|contains, value}}
        trusted: True（默认）用 CDP 真实输入/点击/按键（isTrusted=true）。对 React
          重渲染站点（知乎登录、部分 SaaS）合成事件会被框架冲掉/忽略，必须 trusted；
          普通站（百度/B站）合成事件即可，为提速可显式 trusted=False。
        wait_mode: poll（默认，兼容旧行为，定时重查断言）
                 | event（断言下沉页面，MutationObserver 事件驱动等待——适合提交后
                  等结果出现的异步长等待，DOM 变化即刻唤醒，无定时轮询；
                  注意只对"变化反映到 DOM"的断言有效）
        debug: 1 保留 evidence 并写日志。
        例：设值并断言输入框内容：
          dom_step("set_value",
            selectors={"input":"#inp-query","value":"流浪地球"},
            page_features={"v":"document.querySelector('#inp-query').value"},
            expected_feature={"v":{"op":"eq","value":"流浪地球"}})
        """
        async with DomClient() as client:
            return await dom_act_and_verify(
                client,
                action=action,
                selectors=selectors,
                page_features=page_features,
                expected_feature=expected_feature,
                wait_s=wait_s,
                debug=debug,
                log_prefix="dom_step",
                trusted=trusted,
                wait_mode=wait_mode,
            )

    @mcp.tool()
    async def dom_navigate(site: str, url: str = None, *, debug: int = 0):
        """导航浏览器到指定站点/URL（DOM 通道）。

        site: 已存模板的站点名（用其 home 跳转）或任意标识；url 给了则直接跳 url。
        """
        target = url
        if not target:
            from dom_templates import load_template
            tmpl = load_template(site)
            target = (tmpl or {}).get("home")
        if not target:
            return {"status": "error", "error": "no url and no template home for site",
                    "hint": "pass url= directly, or dom_save_template first"}
        async with DomClient() as client:
            await client.navigate(target)
            await client.wait_page_load()
            await asyncio.sleep(1.5)
            r = {
                "status": "pass",
                "url": await client.eval_js("location.href"),
                "title": await client.get_title(),
            }
            if debug:
                _write_log("dom_navigate",
                           {"tool": "dom_navigate", "site": site, "url": target}, r)
            return r

    @mcp.tool()
    async def dom_explore(
        tag: str = None,
        text_contains: str = None,
        head: int = None,
        tail: int = None,
        include_all: bool = False,
    ):
        """探索当前浏览器页面：返回可交互元素的锚点清单。

        锚点分三类：id（#kw）、name（input[name=q]）、文本（__text__: 或
        __text_nth__:N::，重复文本用组内序号区分）。

        裁剪参数（给 agent 选择权，避免 200+ 元素全塞进上下文）：
          tag: 只返回指定标签（input/button/a/form/select/textarea）
          text_contains: 只返回文本含此关键词的元素
          head: 只返回前 N 个（页面顶部元素）
          tail: 只返回后 N 个（页面底部元素，如确认按钮/弹窗）
          include_all: True 时返回全部（含 no-unique-selector），默认只返锚点
        """
        async with DomClient() as client:
            items = await client.explore()
            total = len(items)

            if not include_all:
                items = [it for it in items if it.get("selector")]
            if tag:
                items = [it for it in items if it.get("tag") == tag]
            if text_contains:
                items = [it for it in items
                         if text_contains in (it.get("text") or "")]

            head_total = len(items)
            if head is not None and head > 0:
                items = items[:head]
            elif tail is not None and tail > 0:
                items = items[-tail:]

            return {
                "url": await client.eval_js("location.href"),
                "title": await client.get_title(),
                "total_elements": total,
                "after_filter": head_total,
                "returned": len(items),
                "head": head,
                "tail": tail,
                "elements": items,
            }

    @mcp.tool()
    async def dom_save_template(site: str, desc: str, steps: list,
                                home: str = None, name: str = None):
        """把现场跑通的一套网页流程固化成可复用模板。

        steps 每项 = {"action": navigate|set_value|click|press_enter|focus,
                      "selectors": {...},
                      "page_features": {特征名: JS表达式},   # 动作后提取
                      "expected_feature": {特征名: {op, value}}}  # 断言
        例：
          [{"action":"navigate","selectors":{"url":"https://.../"},
            "page_features":{"title":"document.title"},
            "expected_feature":{"title":{"op":"exists"}}},
           {"action":"set_value","selectors":{"input":"#q","value":"$QUERY"},
            "page_features":{"input_value":"..."},
            "expected_feature":{"input_value":{"op":"eq","value":"$QUERY"}}}]
        """
        from dom_templates import save_template
        r = save_template(site, desc, steps, home=home, name=name)
        r["status"] = "saved"
        return r

    @mcp.tool()
    async def dom_list_templates():
        """列出已保存的 DOM 流程模板。"""
        from dom_templates import list_templates
        return list_templates()

    @mcp.tool()
    async def dom_run_template(name_or_site: str, params: dict = None, *,
                               debug: int = 0, wait_s: float = 6.0):
        """执行已保存的 DOM 流程模板，逐步验证，任一步 fail 即停。

        name_or_site: 模板文件名或 site 标识。
        params: 替换模板里的 $VAR（如 {"QUERY": "..."}）。
        debug: 1 保留 evidence 并写日志。
        """
        from dom_templates import load_template, _fill
        tmpl = load_template(name_or_site)
        if not tmpl:
            return {"status": "not_found", "name": name_or_site,
                    "available": [t["file"] for t in _list_template_summaries()]}
        filled = _fill(tmpl, params)
        results = []
        async with DomClient() as client:
            for i, step in enumerate(filled.get("steps", [])):
                r = await dom_act_and_verify(
                    client,
                    action=step["action"],
                    selectors=step.get("selectors"),
                    page_features=step.get("page_features"),
                    expected_feature=step.get("expected_feature"),
                    wait_s=wait_s,
                    debug=debug,
                    log_prefix=f"tmpl_{filled.get('site')}_s{i}",
                    trusted=step.get("trusted", True),
                    wait_mode=step.get("wait_mode", "poll"),
                )
                results.append({"step": i, "action": step["action"], **r})
                if r.get("status") != "pass":
                    return {
                        "status": "fail",
                        "failed_step": i,
                        "desc": tmpl.get("desc"),
                        "results": results,
                    }
        return {"status": "pass", "desc": tmpl.get("desc"),
                "steps_total": len(filled.get("steps", [])), "results": results}

    def _list_template_summaries():
        from dom_templates import list_templates
        return list_templates()
