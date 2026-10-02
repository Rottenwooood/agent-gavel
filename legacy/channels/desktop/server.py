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


from .catalog import evaluate_assertions
from .catalog_data import resolve_operation
from .diff import diff_snapshots, classify, meaningful_change
from .normalize import normalize_nodes
from .wait import wait_until_stable


LOG_DIR = os.environ.get(
    "AGENT_GAVEL_LOG_DIR",
    os.path.join(os.environ.get("APPDATA") if os.name == "nt"
                 else os.environ.get("XDG_CONFIG_HOME",
                                     os.path.expanduser("~/.config")),
                 "agent-gavel", "logs"))


class AtspiUnavailableError(RuntimeError):
    """桌面(AT-SPI)通道不可用——没装 computer-use-linux。"""


def _atspi_client():
    """惰性获取 AT-SPI 客户端。未装 computer-use-linux 时抛结构化错误。

    DOM 通道不依赖它；只有调桌面工具才需要。这样 `uvx agent-gavel`
    纯 DOM 用户无需安装 AT-SPI 后端。
    """
    import shutil
    if shutil.which("computer-use-linux") is None:
        raise AtspiUnavailableError(
            "computer-use-linux 未安装——桌面(AT-SPI)通道不可用。"
            "纯 DOM 用法无需它；需要桌面操作请安装 desktop extra"
            "(npm i -g computer-use-linux)")
    from .adapter import ComputerUseClient
    return ComputerUseClient()


def _guard_atspi(fn):
    """装饰器：把 AtspiUnavailableError 转成结构化返回（不给 agent 报错猜原因）。"""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except AtspiUnavailableError as e:
            return {
                "status": "error",
                "reason": "atspi_unavailable",
                "error": str(e),
                "hint": "纯 DOM 用法无需 AT-SPI；桌面操作需安装 computer-use-linux",
            }
    return wrapper


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
        method = args.get("method")
        return _unpack_result(await client.type_text(
            args.get("text", ""), app_id=app_id,
            method=method, window_id=args.get("window_id")))
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
    if action == "move_window":
        mv_args = dict(args)
        if not mv_args.get("window_id"):
            # 没有 window_id 时按 app_id 激活的窗口定位
            aw = await client.activate_window(app_id=app_id)
            if isinstance(aw, dict):
                w = (aw.get("focus") or {}).get("focused_window") or {}
                if w.get("window_id"):
                    mv_args["window_id"] = w["window_id"]
        return _unpack_result(await client.move_window(**mv_args))
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


async def _run_flow(op, params, timeout_s, debug):
    """串行执行 flow 里的子操作。每步可单步 op 或嵌套 dict。"""
    steps = []
    for step in op.get("flow", []):
        s = dict(step)
        # 子步骤里可用 params 替换占位符（action_args/assertions 字符串）
        app_id = (s.get("target") or {}).get("app_id") or (op.get("target") or {}).get("app_id")
        action_args = s.get("action_args") or {}
        verify = {
            "mode": "assert",
            "assertions": s.get("assertions") or [],
        }
        if not verify["assertions"]:
            verify = {"mode": "auto"}
        res = await act_and_verify(
            action=s["action"],
            action_args=action_args,
            app_id=app_id,
            verify=verify,
            timeout_s=timeout_s or s.get("timeout_s", 6.0),
            debug=debug,
        )
        steps.append({"action": s.get("action"), "args": action_args,
                      "status": res.get("status")})
        if res.get("status") == "fail":
            return {"status": "fail", "app": op.get("app"),
                    "operation": op.get("operation"), "failed_step": s.get("action"),
                    "detail": res, "steps": steps}
    return {"status": "pass", "app": op.get("app"), "operation": op.get("operation"),
            "steps": steps}


def _list_operations():
    from .catalog_data import OPERATION_CATALOG
    return [f"{o['app']}:{o['operation']}" for o in OPERATION_CATALOG]





async def _locate_click_point(app_id, role, name, right_pad=150, left_pad=None,
                              y_ratio=0.5, center=False):
    """读原始 AT-SPI 树，找 role+name 匹配的首个元素，算一个点击点。

    返回 {"x","y","found"}。定位逻辑(基于微信实测)：
      默认点元素 bounds 右缘内侧 right_pad 像素、垂直 y_ratio 处。
      微信消息气泡右对齐于行右缘，list item 语义点击(行中心)会落到
      文本左侧空白，故右键/点击要取右缘内偏移。
      center=True：点 bounds 中心(用于对话框按钮等需点中心的元素)。
    """
    async with _atspi_client() as client:
        raw = await client.read_state(app_id=app_id)
    for n in raw or []:
        b = n.get("bounds") or {}
        nm = n.get("name") or ""
        if (not role or n.get("role") == role) and name in nm and b.get("width", 0) > 50:
            if center:
                x = b["x"] + b["width"] // 2
            else:
                x = b["x"] + b["width"] - right_pad
                if left_pad is not None:
                    x = b["x"] + left_pad
            y = b["y"] + int(b["height"] * y_ratio)
            return {"x": int(x), "y": int(y), "found": True,
                    "bounds": {"x": b["x"], "y": b["y"],
                               "width": b["width"], "height": b["height"]}}
    return {"found": False}


async def _resolve_app_pid(client, app_id):
    """app_id -> pid。微信等 app 有多个同名窗口，取第一个有 pid 的。"""
    try:
        wins = await client.list_windows()
        if isinstance(wins, dict):
            wins = wins.get("windows") or []
        for w in wins or []:
            if w.get("app_id") == app_id and w.get("pid"):
                return int(w["pid"])
    except Exception:
        pass
    return None


def _list_desktop_templates():
    from .templates import list_templates
    return list_templates()


if __name__ == "__main__":
    mcp.run()

def register_desktop_tools(mcp):
    """注册桌面(AT-SPI)通道的全部 MCP 工具。"""
    @mcp.tool()
    @_guard_atspi
    async def act_and_verify(
        action: str,
        action_args: dict = None,
        *,
        app_id: str = None,
        pid: int = None,
        window: str = None,
        verify: dict = None,
        timeout_s: float = 8.0,
        debug: int = 0,
        wait_mode: str = "stable",
        before_raw: list = None,
        return_after_raw: bool = False,
    ):
        """执行一个动作并验证其结果，一次调用返回。

        action: click | type | press_key | scroll | activate_window
        action_args: 动作参数。
          click 用 element_index/name/role/text/x/y
          type 用 text（+可选 method/window_id）——见下方"中文输入"策略
          press_key 用 key/times/window_id
        app_id: 目标应用 id（如 firefox_firefox.desktop）
        window: 目标窗口标题（activate_window 用）
        verify: {"mode": "auto"|"assert"|"none", "assertions": [...]}
        timeout_s: 稳定等待上限（秒）
        wait_mode: stable(默认，现有行为——动作后轮询直到树连续 N 拍一致再判定，
          适合无断言的 auto diff，保守但稳)
                 | poll——动作后每拍读树并跑断言，断言 pass 立即返回；
                  有断言时比 stable 快(不等树稳定，只要断言满足就走)；
                  无断言时自动退化为 stable。
        debug: 0=精简返回(无 evidence)，1=含 evidence 并写完整调用日志

        中文输入策略（action=type）：逐键模拟(xdotool)输中文会失败——非 ASCII
        无 keysym。因此 type 默认"含非 ASCII 自动走剪贴板粘贴"：
          method="clipboard"  xsel 写 X CLIPBOARD + Ctrl+V（任意文本可靠）
          method="keys"       逐键模拟（仅纯 ASCII 建议）
          method=省略         自动：文本含非 ASCII → clipboard，否则 keys
        剪贴板方式注意事项（实测微信总结）：
          - 目标 app 读 CLIPBOARD，勿双写 PRIMARY（会触发 X 归属竞争粘旧值）
          - Ctrl+V 不带 app_id（带 app_id 会触发 AT-SPI 焦点校验被误拦），
            定位用 action_args.window_id
          - 调用前应先 activate/click 聚焦输入框（剪贴板粘到当前 X 焦点）
        """
        start = time.monotonic()
        action_args = action_args or {}
        verify = verify or {"mode": "auto"}
        mode = (verify or {}).get("mode", "auto")

        call_record = {
            "tool": "act_and_verify",
            "action": action,
            "action_args": action_args,
            "app_id": app_id,
            "window": window,
            "verify": verify,
            "timeout_s": timeout_s,
            "debug": debug,
            "wait_mode": wait_mode,
        }

        async with _atspi_client() as client:
            # Step 1: 抓 before（归一化 + 保留 hash 供稳定等待复用为 seed）
            t_before = time.monotonic()
            if before_raw is not None:
                if isinstance(before_raw, dict):
                    before_raw = before_raw.get("nodes") or []
                before_raw = before_raw if isinstance(before_raw, list) else None
            if before_raw is None:
                before_raw = await client.read_state(app_id=app_id, pid=pid)
            before_norm = normalize_nodes(before_raw)
            t_after_before = time.monotonic()

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
            t_after_action = time.monotonic()

            # Step 3+4: 等待并判定。
            # poll 模式(有断言)：动作后每拍读树跑断言，全 pass 立即返回——不等树稳定。
            # 无断言或 wait_mode=stable：等树连续 N 拍一致再判（原行为）。
            use_poll = (wait_mode == "poll" and mode == "assert"
                        and (verify or {}).get("assertions"))
            stable, polls, wait_ms, final_hash, final_data = None, 0, 0, None, None

            if use_poll:
                assertions = verify["assertions"]
                after_norm = None
                poll_interval = 0.6  # 桌面读树成本高，间隔放宽
                while time.monotonic() - start < timeout_s:
                    after_raw = await client.read_state(app_id=app_id, pid=pid)
                    after_norm = normalize_nodes(after_raw)
                    polls += 1
                    res = evaluate_assertions(assertions, after_norm["normalized"])
                    if res["status"] == "pass":
                        status = "pass"
                        detail = {"mode": "assert", "poll_passed": True,
                                  "results": res}
                        wait_ms = int((time.monotonic() - start) * 1000)
                        break
                    if res["status"] == "fail":
                        # fail 不一定是终态(异步可能在路上)，继续等直至超时
                        pass
                    await asyncio.sleep(poll_interval)
                else:
                    # 超时：用最后一次抓取判定
                    status, detail = await _do_verify(before_norm, after_norm, verify)
                    detail["poll_timeout"] = True
                    wait_ms = int((time.monotonic() - start) * 1000)
                the_diff = (diff_snapshots(before_norm["normalized"],
                                           after_norm["normalized"])
                            if after_norm is not None else {"total": 0, "items": []})
            else:
                # 原路径：稳定等待 + 一次判定
                async def fetch():
                    return await client.read_state(app_id=app_id, pid=pid)

                # activate/move/press_key 这类动作不改变目标树，before 即稳定态，
                # 跳过稳定确认读树（省一次昂贵的 read_state）。有断言时不跳过。
                has_assertions = bool((verify or {}).get("assertions"))
                no_confirm = (not has_assertions
                              and action in ("activate_window", "move_window", "press_key"))
                stable, polls, wait_ms, final_hash, final_data = await wait_until_stable(
                    fetch, timeout_s=timeout_s,
                    seed_hash=before_norm["signature"],
                    seed_data=before_raw,
                    confirm_changed=not no_confirm,
                )
                if final_data is None:
                    after_raw = await client.read_state(app_id=app_id, pid=pid)
                else:
                    after_raw = final_data
                after_norm = normalize_nodes(after_raw)
                # diff 只算一次，判定和 evidence 共用
                the_diff = diff_snapshots(before_norm["normalized"], after_norm["normalized"])
                status, detail = await _do_verify(before_norm, after_norm, verify,
                                                  precomputed_diff=the_diff)
            t_after_verify = time.monotonic()

            evidence = {
                "diff": the_diff,
                "before_count": len(before_norm["normalized"]),
                "after_count": len(after_norm["normalized"]),
                "before_signature": before_norm["signature"][:16],
                "after_signature": after_norm["signature"][:16],
            }
            screenshot_ref = None
            if debug and status == "ambiguous":
                # 仅 debug=1 时截图辅助判定；常规调用不截图(省一次昂贵截屏, 避免
                # mode=auto 的 ambiguous 结果每次都触发 portal 截屏)
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
                    "phase": {
                        "before_read_ms": int((t_after_before - t_before) * 1000),
                        "action_ms": int((t_after_action - t_after_before) * 1000),
                        "wait_ms": int((t_after_verify - t_after_action) * 1000),
                        "after_verify_ms": int((time.monotonic() - t_after_verify) * 1000),
                    },
                },
            }
            if return_after_raw:
                # 供 desktop_run_template 跨步复用（下一步的 before）
                result["after_raw"] = after_raw

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
    @_guard_atspi
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
        # flow：多步组合操作，串行执行每步并汇总
        if op.get("flow"):
            return await _run_flow(op, params, timeout_s, debug)

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
    @mcp.tool()
    async def list_operations():
        """列出操作目录里已有的 app:operation。"""
        return _list_operations()
    @mcp.tool()
    @_guard_atspi
    async def read_state(app_id: str = None, limit: int = 50):
        """读取当前应用的无障碍树（归一化摘要），调试用。"""
        async with _atspi_client() as client:
            norm = await _read_normalized(client, app_id=app_id)
            return {
                "count": len(norm["normalized"]),
                "nodes": norm["normalized"][:limit],
            }
    @mcp.tool()
    @_guard_atspi
    async def list_windows():
        """列出桌面窗口。"""
        async with _atspi_client() as client:
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

        # 2. Chrome CDP 调试端口是否可达/是否自管（T1: 不再依赖手动开 Chrome）
        from agent_gavel.browser_manager import status as _bm_status
        bm = _bm_status()
        checks["cdp_ready"] = bm["cdp_ready"]
        checks["cdp_browser"] = bm["browser"]
        checks["cdp_owner"] = bm["owner"]
        checks["cdp_mode"] = bm["mode"]
        checks["cdp_pidfile"] = bm["pidfile_pid"]
        checks["chrome_hint"] = (
            "ok: agent-gavel 管理的 Chrome 在跑" if bm["owner"] == "self"
            else "ok: 检测到外部 Chrome" if bm["owner"] == "external"
            else "未启动——首次 DOM 调用会自动拉起" if not bm["cdp_ready"]
            else "unexpected")

        # 3. 模板仓库
        import glob
        from . import templates as _desk_tmpl
        checks["templates"] = sorted(
            os.path.basename(p) for p in glob.glob(os.path.join(LOG_DIR, "..", "templates", "*.json")))

        return {"status": "ok" if checks.get("cdp_ready") else "degraded", "checks": checks}
    @mcp.tool()
    async def desktop_save_template(app: str, desc: str, steps: list,
                                    app_id: str = None, name: str = None):
        """把一套验证过的桌面(AT-SPI)流程固化成可复用模板。

        steps 每项 = act_and_verify 语义：
          {"action": click|type|press_key|activate_window|move_window,
           "action_args": {...},        # 步骤参数，可含 $VAR 占位符
           "assertions": [...],         # 可选：如 [{"type":"element_appears",
                                         #           "role":"push button","name":"发送(S)"}]
           "timeout_s": 6.0}            # 可选
        例：发消息到文件传输助手 =
          [{"action":"click",
            "action_args":{"role":"text","name":"文件传输助手"}},
           {"action":"type",
            "action_args":{"text":"$MSG","method":"clipboard"}},
           {"action":"click",
            "action_args":{"role":"push button","name":"发送(S)"}}]
        """
        from .templates import save_template
        r = save_template(app, desc, steps, app_id=app_id, name=name)
        r["status"] = "saved"
        return r
    @mcp.tool()
    async def desktop_list_templates():
        """列出已保存的桌面流程模板。"""
        from .templates import list_templates
        return list_templates()
    @mcp.tool()
    @_guard_atspi
    async def desktop_run_template(name_or_app: str, params: dict = None, *,
                                   debug: int = 0, timeout_s: float = 8.0):
        """执行已保存的桌面流程模板，逐步验证，任一步 fail 即停。

        name_or_app: 模板文件名或 app 标识。
        params: 替换模板里的 $VAR（如 {"MSG": "你好"}）。
        debug: 1 保留 evidence 并写日志。

        步骤可选 "locate" 字段做动态定位(避免写死坐标)：
          {"action": "click",
           "locate": {"role":"list item", "name":"$MSG", "right_pad":150},
           "action_args": {"button":"right"}}
        执行该步前先读树找 role+name 元素, 把算出的 x/y 注入 action_args。
        """
        from .templates import load_template, _fill
        tmpl = load_template(name_or_app)
        if not tmpl:
            return {"status": "not_found", "name": name_or_app,
                    "available": [t["file"] for t in _list_desktop_templates()]}
        filled = _fill(tmpl, params)
        results = []
        default_app_id = filled.get("app_id") or None
        async with _atspi_client() as client:
            # 会话内解析一次 pid（微信重启后变化），优先 pid 定位避免窗口解析歧义
            pid = None
            if default_app_id:
                pid = await _resolve_app_pid(client, default_app_id)
            before_raw = None
            for i, step in enumerate(filled.get("steps", [])):
                action_args = dict(step.get("action_args") or {})
                app_id = step.get("app_id") or default_app_id
                # 动态定位：读树算点击点注入 action_args
                if step.get("locate"):
                    loc = step["locate"]
                    r = await _locate_click_point(
                        app_id, loc.get("role"), loc.get("name"),
                        right_pad=loc.get("right_pad", 150),
                        left_pad=loc.get("left_pad"),
                        y_ratio=loc.get("y_ratio", 0.5),
                        center=loc.get("center", False))
                    if not r.get("found"):
                        return {"status": "fail", "failed_step": i,
                                "reason": f"locate failed: no element role={loc.get('role')} "
                                          f"name={loc.get('name')}",
                                "desc": tmpl.get("desc"), "results": results}
                    action_args["x"] = r["x"]
                    action_args["y"] = r["y"]
                    results.append({"step": i, "locate": "ok",
                                    "point": (r["x"], r["y"]),
                                    "bounds": r.get("bounds")})
                r = await act_and_verify(
                    action=step["action"],
                    action_args=action_args,
                    app_id=app_id,
                    pid=pid,
                    before_raw=before_raw,
                    return_after_raw=True,
                    verify={"mode": "assert", "assertions": step.get("assertions") or []}
                           if step.get("assertions") else {"mode": "auto"},
                    timeout_s=step.get("timeout_s") or timeout_s,
                    debug=debug,
                )
                results.append({"step": i, "action": step["action"],
                                "status": r.get("status"),
                                "detail": {k: v for k, v in r.items()
                                           if k in ("action", "verification", "error", "cost")}})
                if r.get("status") == "fail":
                    return {"status": "fail", "failed_step": i,
                            "desc": tmpl.get("desc"), "results": results}
                # 上一步的 after 树复用为下一步的 before（省一次读树）。
                # ambiguous(activate/move/无变化)也算成功，after_raw 同样可用。
                if r.get("status") != "fail" and r.get("after_raw"):
                    before_raw = r["after_raw"]
        return {"status": "pass", "desc": tmpl.get("desc"),
                "steps_total": len(filled.get("steps", [])), "results": results}
