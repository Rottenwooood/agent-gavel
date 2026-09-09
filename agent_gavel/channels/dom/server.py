"""DOM 通道的 MCP 工具（与 AT-SPI 桌面工具分离，server.py 只留桌面）。

T2 拆分：DOM 网页操作相关工具全部集中于此，通过 register_dom_tools(mcp)
注入同一个 mcp server。server.py 保留桌面(AT-SPI)工具。
"""

import asyncio

from .adapter import DomClient
from .verify import dom_act_and_verify, _write_log


def _dom_error(e):
    """把 DOM 通道异常转成结构化结果，绝不让 MCP 层包成 'Error executing tool'。

    关键：agent 需要看到失败原因（尤其环境类：Chrome 没起/CDP 断/无页面），
    而不是一个笼统的 UnexpectedToolError 猜半天。
    识别常见环境错误并给 hint：
      - chrome not available / CDP not ready / DISPLAY 未设置 → 环境问题
      - no matching CDP page target → 无页面
      - 其余 → 通用 error
    """
    msg = str(e)
    if any(k in msg for k in ("chrome not available", "CDP not ready", "DISPLAY")):
        return {
            "status": "error",
            "reason": "chrome_env",
            "error": msg,
            "hint": "调试 Chrome 没起来——调 chrome(ensure) 或确认 DISPLAY；"
                    "headless 已禁用，需桌面会话",
        }
    if "no matching CDP page target" in msg:
        return {
            "status": "error",
            "reason": "no_page_target",
            "error": msg,
            "hint": "Chrome 在跑但没有可用页面 tab——导航一个 URL 或开新 tab",
        }
    return {"status": "error", "reason": "dom_error", "error": msg}


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
        from agent_gavel.browser_manager import (stop_own, status as bm_status,
                                                 ensure_chrome)
        if action == "stop":
            return stop_own()
        if action == "status":
            return bm_status()
        return ensure_chrome()

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
        strict: bool = False,
        redact: list = None,
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
        strict: True 时不自动降级重试，失败直接返回（测试/调试用，暴露真实 fail）。
        debug: 1 保留 evidence 并写日志。
        例：设值并断言输入框内容：
           dom_step("set_value",
             selectors={"input":"#inp-query","value":"流浪地球"},
             page_features={"v":"document.querySelector('#inp-query').value"},
             expected_feature={"v":{"op":"eq","value":"流浪地球"}})
        redact: 可选，声明 selectors 里哪些字段值是敏感值(不落日志/不回传)，
          如 ["value"]（填密码时用）。dom_run_template 的 $PASSWORD 等自动脱敏。
        """
        # 收集要脱敏的值（redact 列出的 selectors 字段的实际值）
        redact_values = []
        if redact and selectors:
            for k in redact:
                v = (selectors or {}).get(k)
                if v:
                    redact_values.append(str(v))
        try:
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
                    strict=strict,
                    redact_values=redact_values,
                )
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_navigate(site: str, url: str = None, *, debug: int = 0):
        """导航浏览器到指定站点/URL（DOM 通道）。

        site: 已存模板的站点名（用其 home 跳转）或任意标识；url 给了则直接跳 url。
        """
        target = url
        if not target:
            from .templates import load_template
            tmpl = load_template(site)
            target = (tmpl or {}).get("home")
        if not target:
            return {"status": "error", "error": "no url and no template home for site",
                    "hint": "pass url= directly, or dom_save_template first"}
        try:
            async with DomClient() as client:
                await client.navigate(target)
                await client.wait_page_load()
                r = {
                    "status": "pass",
                    "url": await client.eval_js("location.href"),
                    "title": await client.get_title(),
                }
                if debug:
                    _write_log("dom_navigate",
                               {"tool": "dom_navigate", "site": site, "url": target}, r)
                return r
        except Exception as e:
            return _dom_error(e)

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
        try:
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
        except Exception as e:
            return _dom_error(e)

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
        from .templates import save_template
        r = save_template(site, desc, steps, home=home, name=name)
        r["status"] = "saved"
        return r

    @mcp.tool()
    async def dom_list_templates():
        """列出已保存的 DOM 流程模板。"""
        from .templates import list_templates
        return list_templates()

    @mcp.tool()
    async def dom_template_stats():
        """查看所有 DOM 模板的失效检测 stats。

        返回 {模板文件名: {consecutive_fails, suspected, last, last_at}}。
        suspected=true 表示连续失败达阈值、模板疑似失效。
        """
        from .templates import all_stats
        return all_stats()

    @mcp.tool()
    async def dom_run_template(name_or_site: str, params: dict = None, *,
                               debug: int = 0, wait_s: float = 6.0,
                               strict: bool = False):
        """执行已保存的 DOM 流程模板，逐步验证，任一步 fail 即停。

        name_or_site: 模板文件名或 site 标识。
        params: 替换模板里的 $VAR（如 {"QUERY": "..."}）。
        strict: True 时不自动降级重试（测试/调试用，暴露模板真实 fail）。
        debug: 1 保留 evidence 并写日志。
        失效检测：每跑完记一次连续失败；连续失败>=3 标 suspected，
        pass 清零。suspected 模板执行时返回结果顶部带 warning。
        """
        from .templates import (resolve_template, _fill, record_run, get_stats,
                                is_sensitive_var)
        template_file, tmpl = resolve_template(name_or_site)
        if not tmpl:
            return {"status": "not_found", "name": name_or_site,
                    "available": [t["file"] for t in _list_template_summaries()]}
        # 校验模板声明需要的参数是否齐（params 自动从 $VAR 提取）
        need = tmpl.get("params") or []
        params = params or {}
        missing = [p for p in need if p not in params]
        if missing:
            return {"status": "fail", "reason": "missing_params",
                    "missing": missing, "needs": need,
                    "desc": tmpl.get("desc"),
                    "hint": f"dom_run_template 需传参数: {need}"}
        # 敏感参数(密码/token 等)值收集——供脱敏, 不落日志/不回传
        redact_values = [str(params[p]) for p in params
                         if is_sensitive_var(p) and params[p]]
        # 执行前查失效状态
        pre = get_stats(template_file)
        warning = None
        if pre.get("suspected"):
            warning = (f"模板疑似失效(连续失败 {pre.get('consecutive_fails')} 次)——"
                       f"建议 dom_explore 重新探索后 dom_save_template 覆盖")
        filled = _fill(tmpl, params)
        results = []
        try:
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
                        strict=strict,
                        redact_values=redact_values,
                    )
                    results.append({"step": i, "action": step["action"], **r})
                    if r.get("status") != "pass":
                        record_run(template_file, "fail")
                        out = {
                            "status": "fail",
                            "failed_step": i,
                            "desc": tmpl.get("desc"),
                            "results": results,
                        }
                        if warning:
                            out["warning"] = warning
                        return out
            record_run(template_file, "pass")
            out = {"status": "pass", "desc": tmpl.get("desc"),
                   "steps_total": len(filled.get("steps", [])), "results": results}
            if warning:
                out["warning"] = warning
            return out
        except Exception as e:
            env = _dom_error(e)
            env["failed_step"] = len(results)
            env["desc"] = tmpl.get("desc")
            env["results"] = results
            return env

    def _list_template_summaries():
        from .templates import list_templates
        return list_templates()
