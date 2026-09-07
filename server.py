"""闭环原语 MCP server：act_and_verify。

一次调用 = 抓 before → 执行动作 → 稳定等待 → 判定（断言/auto diff）
→ ambiguous 升级（截图给模型）→ 返回 (status, diff, evidence)。

对外暴露工具：
  act_and_verify  主原语
  read_state      读当前状态（归一化树摘要），调试用
  list_windows    透传
  doctor          自检
"""

import asyncio
import datetime
import json
import os
import time

from mcp.server.mcpserver import MCPServer

from adapter import ComputerUseClient
from catalog import evaluate_assertions
from catalog_data import resolve_operation
from diff import diff_snapshots, classify, meaningful_change
from dom_verify import dom_act_and_verify
from normalize import normalize_nodes
from wait import wait_until_stable

mcp = MCPServer("agent-gavel")

LOG_DIR = os.environ.get("AGENT_GAVEL_LOG_DIR", "/home/c6h4o2/agent-gavel/logs")


def _write_call_log(call, result):
    """debug=1 时：把一次调用(参数+完整返回)记录为独立日志文件。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = os.path.join(LOG_DIR, f"call_{ts}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump({"call": call, "result": result}, f,
                      ensure_ascii=False, indent=2)
    except Exception:
        pass


def _unpack_result(res):
    """MCP call 结果可能是 dict/str/复杂对象，归一化成 dict。"""
    if isinstance(res, dict):
        return res
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return {"raw": res}
    return {"raw": str(res)}


async def _read_normalized(client, app_id=None):
    raw = await client.read_state(app_id=app_id)
    return normalize_nodes(raw)


async def _execute_action(client, action, args, app_id=None):
    """执行动作，返回动作执行是否成功。"""
    if action == "click":
        click_args = dict(args)
        if app_id:
            click_args["app_id"] = app_id
        res = await client.click(**click_args)
        return _unpack_result(res)
    if action == "type":
        return _unpack_result(await client.type_text(args.get("text", ""), app_id=app_id))
    if action == "press_key":
        key = args.get("key", "")
        times = int(args.get("times", 1))
        last = None
        for _ in range(max(1, times)):
            last = _unpack_result(await client.press_key(key, app_id=app_id))
            if times > 1:
                await asyncio.sleep(0.5)
        return last
    if action == "scroll":
        return _unpack_result(await client.scroll(
            args.get("direction", "down"), args.get("pages", 1.0), app_id=app_id))
    if action == "activate_window":
        aw_args = dict(args)
        if app_id:
            aw_args["app_id"] = app_id
        return _unpack_result(await client.activate_window(**aw_args))
    raise ValueError(f"unknown action: {action}")


async def _do_verify(before_norm, after_norm, verify, precomputed_diff=None):
    """判定：assert / auto / none。返回 (status, detail)。

    detail 只放"结论"（mode / 判定依据的简要结果），
    不重复放 diff——diff 属于 evidence，只算一次由调用方持有。
    """
    mode = (verify or {}).get("mode", "auto")
    if mode == "none":
        return "pass", {"mode": "none"}

    if mode == "assert":
        assertions = (verify or {}).get("assertions") or []
        if not assertions:
            return "ambiguous", {"mode": "assert", "detail": "no assertions provided"}
        result = evaluate_assertions(assertions, after_norm["normalized"])
        return result["status"], {"mode": "assert", "results": result}

    # auto: 用结构 diff 判定。diff 只算一次，由调用方传入复用。
    d = precomputed_diff if precomputed_diff is not None else diff_snapshots(
        before_norm["normalized"], after_norm["normalized"])
    if meaningful_change(d, min_structural=1):
        return "pass", {"mode": "auto", "class": classify(d), "total": d["total"]}
    return "ambiguous", {"mode": "auto", "class": classify(d), "total": d["total"]}


@mcp.tool()
async def act_and_verify(
    action: str,
    action_args: dict = None,
    *,
    app_id: str = None,
    window: str = None,
    verify: dict = None,
    timeout_s: float = 8.0,
    debug: int = 0,
):
    """执行一个动作并验证其结果，一次调用返回。

    action: click | type | press_key | scroll | activate_window
    action_args: 动作参数（click 用 element_index/name/role/text；type 用 text；等）
    app_id: 目标应用 id（如 firefox_firefox.desktop）
    window: 目标窗口标题（activate_window 用）
    verify: {"mode": "auto"|"assert"|"none", "assertions": [...]}
    timeout_s: 稳定等待上限（秒）
    debug: 0=精简返回(无 evidence)，1=含 evidence 并写完整调用日志
    """
    start = time.monotonic()
    action_args = action_args or {}
    verify = verify or {"mode": "auto"}

    call_record = {
        "tool": "act_and_verify",
        "action": action,
        "action_args": action_args,
        "app_id": app_id,
        "window": window,
        "verify": verify,
        "timeout_s": timeout_s,
        "debug": debug,
    }

    async with ComputerUseClient() as client:
        # Step 1: 抓 before（归一化 + 保留 hash 供稳定等待复用为 seed）
        before_raw = await client.read_state(app_id=app_id)
        before_norm = normalize_nodes(before_raw)

        # Step 2: 执行动作
        try:
            action_result = await _execute_action(client, action, action_args, app_id=app_id)
        except Exception as e:
            result = {
                "status": "not_executed",
                "error": str(e),
                "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)},
            }
            if debug:
                _write_call_log(call_record, result)
            return result

        # Step 3: 稳定等待（复用 before 作 seed，避免首轮白等）
        async def fetch():
            return await client.read_state(app_id=app_id)

        stable, polls, wait_ms, final_hash, final_data = await wait_until_stable(
            fetch, timeout_s=timeout_s,
            seed_hash=before_norm["signature"],
            seed_data=before_raw,
        )

        # Step 4: 判定。after = 稳定等待最后一次抓取，无需重复读
        if final_data is None:
            after_raw = await client.read_state(app_id=app_id)
        else:
            after_raw = final_data
        after_norm = normalize_nodes(after_raw)
        # diff 只算一次，判定和 evidence 共用
        the_diff = diff_snapshots(before_norm["normalized"], after_norm["normalized"])
        status, detail = await _do_verify(before_norm, after_norm, verify,
                                          precomputed_diff=the_diff)

        evidence = {
            "diff": the_diff,
            "before_count": len(before_norm["normalized"]),
            "after_count": len(after_norm["normalized"]),
            "before_signature": before_norm["signature"][:16],
            "after_signature": after_norm["signature"][:16],
        }
        screenshot_ref = None
        if status == "ambiguous":
            shot = await client.screenshot(app_id=app_id, format="jpeg", quality=70)
            screenshot_ref = _unpack_result(shot)

        result = {
            "status": status,
            "action": {"name": action, "result": action_result},
            "verification": {
                **detail,
                "stable": stable,
                "polls": polls,
            },
            "evidence": evidence,
            "screenshot": screenshot_ref,
            "cost": {
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "stable_wait_ms": wait_ms,
            },
        }

        if debug:
            # 日志：总是含 evidence + screenshot 完整结果
            _write_call_log(call_record, result)
            return result  # debug=1 保留完整结果给调用方
        else:
            # 精简：去掉 evidence（和截图路径），只留判定结论
            slim = {k: v for k, v in result.items() if k != "evidence"}
            slim["evidence"] = {"stripped": True,
                                "note": "pass debug=1 to receive diff evidence"}
            return slim


@mcp.tool()
async def run_operation(app: str, operation: str, params: dict = None,
                        timeout_s: float = None, debug: int = 0):
    """从操作目录查 app+operation 的断言模板，执行动作并验证。

    例：run_operation("firefox", "type_search", {"QUERY": "opencode"})
    目录里没有的 app/operation 返回 not_found。
    debug: 1 时保留 evidence 并写完整调用日志。
    """
    op = resolve_operation(app, operation, params)
    if op is None:
        return {
            "status": "not_found",
            "app": app,
            "operation": operation,
            "available": _list_operations(),
        }
    target = op.get("target") or {}
    app_id = target.get("app_id")
    verify = {
        "mode": "assert",
        "assertions": op.get("assertions") or [],
    }
    if not verify["assertions"]:
        verify = {"mode": "auto"}
    return await act_and_verify(
        action=op["action"],
        action_args=op.get("action_args") or {},
        app_id=app_id,
        verify=verify,
        timeout_s=timeout_s or op.get("timeout_s", 6.0),
        debug=debug,
    )


def _list_operations():
    from catalog_data import OPERATION_CATALOG
    return [f"{o['app']}:{o['operation']}" for o in OPERATION_CATALOG]


@mcp.tool()
async def list_operations():
    """列出操作目录里已有的 app:operation。"""
    return _list_operations()


@mcp.tool()
async def read_state(app_id: str = None, limit: int = 50):
    """读取当前应用的无障碍树（归一化摘要），调试用。"""
    async with ComputerUseClient() as client:
        norm = await _read_normalized(client, app_id=app_id)
        return {
            "count": len(norm["normalized"]),
            "nodes": norm["normalized"][:limit],
        }


@mcp.tool()
async def list_windows():
    """列出桌面窗口。"""
    async with ComputerUseClient() as client:
        return await client.list_windows()


@mcp.tool()
async def doctor():
    """自检：报告 agent-gavel 各通道就绪状态（不依赖嵌套子进程）。"""
    import shutil
    import glob

    checks = {}

    # 1. AT-SPI 后端 binary 是否可执行
    cul = shutil.which("computer-use-linux")
    checks["atspi_binary"] = bool(cul)
    checks["atspi_binary_path"] = cul

    # 2. Chrome CDP 调试端口是否可达
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2) as r:
            import json as _json
            v = _json.loads(r.read())
            checks["cdp_ready"] = True
            checks["cdp_browser"] = v.get("Browser")
    except Exception as e:
        checks["cdp_ready"] = False
        checks["cdp_error"] = str(e)[:120]

    # 3. 模板仓库
    checks["templates"] = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(LOG_DIR, "..", "templates", "*.json")))

    return {"status": "ok" if checks.get("cdp_ready") else "degraded", "checks": checks}


# ---------------- DOM 通道（网页操作） ----------------

from dom_adapter import DomClient


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
        # 直接导航并等加载
        await client.navigate(target)
        await client.wait_page_load()
        await asyncio.sleep(1.5)
        r = {
            "status": "pass",
            "url": await client.eval_js("location.href"),
            "title": await client.get_title(),
        }
        if debug:
            _write_call_log({"tool": "dom_navigate", "site": site, "url": target}, r)
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

        # 过滤：默认只留 unique 锚点
        if not include_all:
            items = [it for it in items if it.get("selector")]
        if tag:
            items = [it for it in items if it.get("tag") == tag]
        if text_contains:
            items = [it for it in items
                     if text_contains in (it.get("text") or "")]

        head_total = len(items)
        # 裁剪：head 或 tail 二选一
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


if __name__ == "__main__":
    mcp.run()
