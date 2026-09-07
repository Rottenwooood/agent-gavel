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
import json
import time

from mcp.server.mcpserver import MCPServer

from adapter import ComputerUseClient
from catalog import evaluate_assertions
from catalog_data import resolve_operation
from diff import diff_snapshots, classify, meaningful_change
from normalize import normalize_nodes
from wait import wait_until_stable

mcp = MCPServer("agent-claw")


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
):
    """执行一个动作并验证其结果，一次调用返回。

    action: click | type | press_key | scroll | activate_window
    action_args: 动作参数（click 用 element_index/name/role/text；type 用 text；等）
    app_id: 目标应用 id（如 firefox_firefox.desktop）
    window: 目标窗口标题（activate_window 用）
    verify: {"mode": "auto"|"assert"|"none", "assertions": [...]}
    timeout_s: 稳定等待上限（秒）
    """
    start = time.monotonic()
    action_args = action_args or {}
    verify = verify or {"mode": "auto"}

    async with ComputerUseClient() as client:
        # Step 1: 抓 before
        before_norm = await _read_normalized(client, app_id=app_id)

        # Step 2: 执行动作
        try:
            action_result = await _execute_action(client, action, action_args, app_id=app_id)
        except Exception as e:
            return {
                "status": "not_executed",
                "error": str(e),
                "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)},
            }

        # Step 3: 稳定等待
        async def fetch():
            return await client.read_state(app_id=app_id)

        stable, polls, wait_ms, final_hash = await wait_until_stable(
            fetch, timeout_s=timeout_s,
        )

        # Step 4+5: 判定 + ambiguous 升级
        after_raw = await client.read_state(app_id=app_id)
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

        return {
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


@mcp.tool()
async def run_operation(app: str, operation: str, params: dict = None, timeout_s: float = None):
    """从操作目录查 app+operation 的断言模板，执行动作并验证。

    例：run_operation("firefox", "type_search", {"QUERY": "opencode"})
    目录里没有的 app/operation 返回 not_found。
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
    """自检：报告 computer-use-linux 后端就绪状态。"""
    async with ComputerUseClient() as client:
        return await _unpack_result(await client._call("doctor"))


if __name__ == "__main__":
    mcp.run()
