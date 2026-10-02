"""agent-gavel DOM 通道冒烟测试脚本（直连，不经过 MCP 客户端）。

用法: cd agent-gavel && uv run python3 tests/dom_smoke.py
覆盖: 环境自检 → 导航 → 单步断言 → 中文输入 → 完整模板 → 失效记录 → 失败诊断
每步用 time.perf_counter() 分段计时，末尾汇总表。全过打印 ALL PASS。
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gavel.channels.dom.adapter import DomClient
from agent_gavel.channels.dom.verify import dom_act_and_verify

# 段后打点器：某段操作"刚完成"时调 _tock("段名")，_start() 在开头设起点。
# 每段耗时 = 该段操作从开始到完成，归属正确（在操作后调用 _tock）。
_times = []
_t0 = None
_last_name = None


def _start():
    global _t0, _last_name
    _t0 = time.perf_counter()
    _last_name = "(启动)"


def _tock(name):
    """标记名为 name 的这段刚完成。耗时会记给 (上一次的 name, 间隔)？不——
    记给本次 name：间隔 = 从上次 _tock/_start 到这次 _tock。"""
    global _t0
    now = time.perf_counter()
    if _t0 is not None:
        _times.append((name, (now - _t0) * 1000))
    _t0 = now


def _finish():
    pass  # 各段都在完成时 _tock 过，无残余


def _fmt(ms):
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.0f}ms"


def _report():
    print("\n===== 耗时汇总 =====")
    total = 0
    for name, ms in _times:
        total += ms
        print(f"  {name:<40} {_fmt(ms):>8}")
    print(f"  {'TOTAL(各段之和)':<40} {_fmt(total):>8}")
    print("=" * 28)


def ok(name, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name} {extra}")
    if not cond:
        raise AssertionError(f"{name} failed {extra}")


async def _step(name):
    print(f"\n== {name} ==", flush=True)


async def main():
    _start()

    # ---- 1. 环境自检：Chrome 自动拉起 ----
    await _step("1. 环境自检: DomClient 连接(自动拉起可见 Chrome)")
    async with DomClient() as client:
        _tock("1 连接+自拉Chrome(可见窗口)")
        info = await client.eval_js("navigator.userAgent")
        _tock("1b eval userAgent")
        print(f"  Chrome UA: {info[:80]}...")

        # ---- 2. 导航（分 navigate / wait / 标题读取）----
        await _step("2. 导航到必应")
        await client.navigate("https://cn.bing.com")
        _tock("2a navigate 必应(发命令)")
        title = await client.get_title()
        _tock("2b get_title")
        ok("必应标题", "必应" in title, f"got={title!r}")

        # ---- 3. 单步 set_value + 断言 ----
        await _step("3. set_value 填搜索框 + 断言")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#sb_form_q", "value": "opencode"},
            page_features={"v": "document.querySelector('#sb_form_q').value"},
            expected_feature={"v": {"op": "eq", "value": "opencode"}},
            wait_s=4)
        _tock("3 set_value 填词+断言")
        ok("填词断言 pass", r["status"] == "pass", f"status={r['status']} "
           f"[工具cost {r.get('cost', {}).get('elapsed_ms')}ms]")

        # ---- 4. 中文输入 ----
        await _step("4. 中文输入(流浪地球)")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#sb_form_q", "value": "流浪地球"},
            page_features={"v": "document.querySelector('#sb_form_q').value"},
            expected_feature={"v": {"op": "eq", "value": "流浪地球"}},
            wait_s=4)
        _tock("4 set_value 中文+断言")
        ok("中文断言 pass", r["status"] == "pass", f"status={r['status']}")

        # ---- 5. 提交 + 整页跳转断言 ----
        await _step("5. 回车提交, 断言跳转结果页")
        r = await dom_act_and_verify(
            client, action="press_enter",
            page_features={"title": "document.title"},
            expected_feature={"title": {"op": "contains", "value": "流浪地球"}},
            wait_s=8)
        _tock("5 press_enter 提交+跳转断言")
        ok("提交跳转 pass", r["status"] == "pass",
           f"status={r['status']} title={(r.get('verification') or {}).get('title','')[:30]} "
           f"[工具cost {r.get('cost', {}).get('elapsed_ms')}ms]")

        # ---- 6. 失败诊断：错误选择器 ----
        await _step("6. 失败诊断: 错误选择器(应带 reason)")
        r = await dom_act_and_verify(
            client, action="set_value",
            selectors={"input": "#definitely-not-exists", "value": "x"},
            page_features={"v": "1"},
            expected_feature={"v": {"op": "eq", "value": "2"}},
            wait_s=1, strict=True)
        _tock("6 错误选择器(期望fail)")
        ok("错误选择器返回 fail", r["status"] == "fail")
        ok("带 reason", r.get("reason") in ("action_failed", "assertion_timeout"),
           f"reason={r.get('reason')}")

    # ---- 7. 完整模板 ----
    await _step("7. dom_run_template bing_search")
    from agent_gavel.channels.dom.templates import resolve_template, _fill
    from agent_gavel.channels.dom.templates import record_run

    f, tmpl = resolve_template("bing_search")
    ok("模板可解析", tmpl is not None, f"file={f}")
    filled = _fill(tmpl, {"QUERY": "agent-gavel"})
    async with DomClient() as client:
        _tock("7a 模板: DomClient 连接")
        for i, step in enumerate(filled["steps"]):
            r = await dom_act_and_verify(
                client, action=step["action"],
                selectors=step.get("selectors"),
                page_features=step.get("page_features"),
                expected_feature=step.get("expected_feature"),
                wait_s=8, log_prefix=f"smoke_s{i}")
            ok(f"模板 step{i} pass", r["status"] == "pass", f"status={r['status']} "
               f"[工具cost {r.get('cost', {}).get('elapsed_ms')}ms]")
            _tock(f"7b 模板 step{i}: {step['action']}")
        record_run(f, "pass")

    _report()
    print("\nALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
