"""agent-gavel DOM 多模板循环压测（直连，不经过 MCP 客户端）。

用法: cd agent-gavel && uv run python3 tests/dom_smoke_loop.py [轮数]
默认 3 轮，在多个模板间循环跑（bing/douban/baike/cnblogs/runoob，
覆盖中英文关键词）。统计每轮每模板耗时，末尾汇总成功率 + 平均耗时。

目的：观察模板在"连续多次运行"下的稳定性（不是单次跑通），
暴露偶发失败（反爬/时序/断言抖动）。
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gavel.channels.dom.adapter import DomClient
from agent_gavel.channels.dom.verify import dom_act_and_verify
from agent_gavel.channels.dom.templates import resolve_template, _fill

# (模板, 参数)——搜索类，中英文混合，都能自动跑通无需人工
CASES = [
    ("bing_search", {"QUERY": "agent-gavel"}),
    ("douban_movie_search", {"QUERY": "流浪地球"}),
    ("baike_search", {"QUERY": "图灵机"}),
    ("cnblogs_search", {"QUERY": "asyncio"}),
    ("runoob_search", {"QUERY": "python"}),
]


def _fmt(ms):
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.0f}ms"


async def run_template(client, name, params):
    """跑一个模板，返回 (ok, 总耗时ms)。"""
    t0 = time.perf_counter()
    f, tmpl = resolve_template(name)
    if not tmpl:
        return False, int((time.perf_counter() - t0) * 1000), "template not found"
    filled = _fill(tmpl, params)
    for i, step in enumerate(filled["steps"]):
        r = await dom_act_and_verify(
            client, action=step["action"],
            selectors=step.get("selectors"),
            page_features=step.get("page_features"),
            expected_feature=step.get("expected_feature"),
            wait_s=10, log_prefix=f"loop_{name}_r{i}")
        if r.get("status") != "pass":
            return False, int((time.perf_counter() - t0) * 1000), \
                f"step{i} {r.get('status')} reason={r.get('reason')}"
    return True, int((time.perf_counter() - t0) * 1000), ""


async def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"=== 多模板循环压测: {len(CASES)} 模板 × {rounds} 轮 ===\n")

    # 统计: {模板名: {"ok": int, "times": [每轮耗时ms]}}
    stats = {name: {"ok": 0, "times": []} for name, _ in CASES}
    fails = []

    async with DomClient() as client:
        for rnd in range(1, rounds + 1):
            print(f"--- 第 {rnd}/{rounds} 轮 ---", flush=True)
            for name, params in CASES:
                ok, ms, err = await run_template(client, name, params)
                stats[name]["times"].append(ms)
                if ok:
                    stats[name]["ok"] += 1
                    print(f"  PASS {name:<22} {_fmt(ms):>8}", flush=True)
                else:
                    fails.append((rnd, name, err))
                    print(f"  FAIL {name:<22} {_fmt(ms):>8}  {err}", flush=True)
            if rnd < rounds:
                await asyncio.sleep(1.0)

    # ---- 汇总 ----
    print("\n===== 汇总 =====")
    total_all = 0
    total_calls = 0
    print(f"  {'模板':<24} {'通过率':<8} {'平均':>8} {'最快':>8} {'最慢':>8}")
    for name, _ in CASES:
        s = stats[name]
        times = s["times"]
        total_calls += len(times)
        total_all += sum(times)
        ok_avg = sum(times[:s["ok"]]) / max(s["ok"], 1)
        # 平均只算成功轮（失败轮被策略重试拉长，不代表正常耗时）
        avg = sum(times[:s["ok"]]) / s["ok"] if s["ok"] else float("nan")
        print(f"  {name:<24} {s['ok']}/{len(times):<4} "
              f"{_fmt(avg) if s['ok'] else '--':>8} "
              f"{_fmt(min(times)):>8} {_fmt(max(times)):>8}")

    print(f"\n  总调用 {total_calls} 次, 失败 {len(fails)} 次")
    if fails:
        print("  失败明细:")
        for rnd, name, err in fails:
            print(f"    第{rnd}轮 {name}: {err}")
        print("\nRESULT: FAIL")
    else:
        print(f"  平均每次成功调用: {_fmt(total_all/total_calls)}")
        print("\nRESULT: ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
