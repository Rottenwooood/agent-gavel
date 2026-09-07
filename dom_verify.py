"""DOM 版闭环执行器：复用 agent-claw 的验证框架，但操作网页 DOM。

与 server.py 的 act_and_verify（AT-SPI/桌面）并列。共用：
  normalize / diff / wait / catalog 逻辑

每个站点（baidu/bing/google）的"页面状态"是一组 key=value：
  {"url": ..., "title": ..., "kw_value": ..., "has_result": bool}
页面本身没有可复用的无障碍树，所以用"特征提取"代替 diff——
即调用方声明"这个动作应该改变哪个特征"，然后检查该特征。
"""

import asyncio
import datetime
import json
import os
import time

from dom_adapter import DomClient

LOG_DIR = os.environ.get("AGENT_CLAW_LOG_DIR", "/home/c6h4o2/agent-claw/logs")


def _write_log(prefix, call, result):
    """记录一次 DOM 调用（debug=1 时）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        with open(os.path.join(LOG_DIR, f"{prefix}_{ts}.json"), "w", encoding="utf-8") as f:
            json.dump({"call": call, "result": result}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _strip_evidence(result):
    """debug=0 时去掉 evidence。"""
    if "evidence" in result:
        del result["evidence"]
        result["evidence"] = {"stripped": True, "note": "pass debug=1 for evidence"}
    return result


async def dom_act_and_verify(
    client: DomClient,
    *,
    action: str,
    selectors: dict = None,
    page_features: dict = None,
    expected_feature: dict = None,
    wait_s: float = 8.0,
    debug: int = 0,
    log_prefix: str = "op",
    trusted: bool = False,
):
    """执行网页动作 + 验证特征变化，一次调用返回。

    action: set_value | click | press_enter | navigate | focus
    selectors: {目标名: CSS选择器}，action 用
    page_features: 动作后应检查的页面特征，dict {名: JS表达式返回标量}
    expected_feature: 断言期望，{名: {op: eq|neq|exists|not_exists, value: ...}}
    trusted: True 用 CDP 真实输入/点击/按键（isTrusted=true），对 React 重渲染
        站点（知乎等）合成事件会被冲掉/忽略，必须开。默认 False 保速度。
    """
    start = time.monotonic()
    call = {
        "action": action,
        "selectors": selectors,
        "page_features": page_features,
        "expected_feature": expected_feature,
        "debug": debug,
        "trusted": trusted,
    }
    result = {}

    try:
        # ---- 执行动作 ----
        sel = selectors or {}
        if action == "set_value":
            target = sel.get("input")
            if not target:
                raise ValueError("set_value needs selectors.input")
            r = await client.set_value(target, sel.get("value", ""), trusted=trusted)
            ok = isinstance(r, dict) and r.get("ok")
        elif action == "click":
            target = sel.get("target") or sel.get("input")
            if not target:
                raise ValueError("click needs selectors.target")
            r = await client.click(target, trusted=trusted)
            ok = isinstance(r, dict) and r.get("ok")
        elif action == "press_enter":
            r = await client.press_enter(trusted=trusted)
            ok = isinstance(r, dict) and r.get("ok")
        elif action == "navigate":
            r = await client.navigate(sel.get("url", ""))
            await client.wait_page_load()
            ok = True
        elif action == "focus":
            target = sel.get("input")
            r = await client.focus(target)
            ok = isinstance(r, dict) and r.get("ok")
        elif action == "clear":
            target = sel.get("input") or sel.get("target")
            if not target:
                raise ValueError("clear needs selectors.input")
            r = await client.clear(target, trusted=trusted)
            ok = isinstance(r, dict) and r.get("ok")
        else:
            raise ValueError(f"unknown dom action: {action}")

        if not ok:
            result = {"status": "fail", "action_error": r,
                      "action": {"name": action}}
            if debug:
                _write_log(log_prefix + "_" + action, call, result)
            return result

        # ---- 稳定等待（页面动作常异步，等 2 拍）----
        await asyncio.sleep(min(wait_s, 3.0))

        # ---- 验证特征 ----
        evidence = {}
        if page_features:
            for fname, expr in page_features.items():
                try:
                    evidence[fname] = await client.eval_js(expr)
                except Exception as e:
                    evidence[fname] = f"<error: {e}>"

        status = "pass"
        detail = {"mode": "feature"}
        if expected_feature:
            # 检查每个期望
            checks = []
            all_pass = True
            for fname, expect in expected_feature.items():
                actual = evidence.get(fname)
                op = expect.get("op", "eq")
                if op == "eq":
                    passed = (actual == expect.get("value"))
                elif op == "neq":
                    passed = (actual != expect.get("value"))
                elif op == "exists":
                    passed = (actual not in (None, "", False))
                elif op == "not_exists":
                    passed = (actual in (None, "", False))
                elif op == "contains":
                    passed = (expect.get("value") in str(actual))
                else:
                    passed = False
                checks.append({"feature": fname, "op": op,
                               "actual": actual,
                               "expected": expect.get("value"),
                               "passed": passed})
                if not passed:
                    all_pass = False
            status = "pass" if all_pass else "fail"
            detail["checks"] = checks

        result = {
            "status": status,
            "action": {"name": action, "result": r},
            "verification": {
                **detail,
                "url": await client.eval_js("location.href"),
                "title": await client.get_title(),
            },
            "evidence": evidence,
            "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)},
        }
    except Exception as e:
        result = {"status": "error", "error": str(e),
                  "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)}}

    if debug:
        _write_log(log_prefix, call, result)
        return result
    return _strip_evidence(result)


def _write_log(prefix, call, result):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        with open(os.path.join(LOG_DIR, f"{prefix}_{ts}.json"), "w", encoding="utf-8") as f:
            json.dump({"call": call, "result": result}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
