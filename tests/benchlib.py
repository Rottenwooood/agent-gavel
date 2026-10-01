"""性能基准库（docs/tech-plan.md §11/§12）。

被 tests/test_perf.py（门槛断言）与 tests/bench_runtime.py（独立报告）复用。
测量维度：
  - 操作延迟分布 p50/p95/p99（title/eval/locator/click/fill/snapshot/explore/navigate）
  - 冷启动 / 热连接 / 崩溃恢复
  - 并发：同页串行、跨页并行、多 session 并行
  - 资源：1000 次操作前后 page/context 计数、进程 RSS 增长
"""

import asyncio
import os
import statistics
import time

import psutil

from agent_gavel.tools.common import backend


def stats(lat_ms):
    lat = sorted(lat_ms)
    def pct(p):
        if not lat:
            return None
        i = int(round((p / 100) * (len(lat) - 1)))
        return round(lat[max(0, min(len(lat) - 1, i))], 3)
    return {
        "n": len(lat),
        "p50": pct(50), "p95": pct(95), "p99": pct(99),
        "mean": round(statistics.fmean(lat), 3) if lat else None,
        "min": round(lat[0], 3) if lat else None,
        "max": round(lat[-1], 3) if lat else None,
    }


async def measure(fn, n=30, warmup=3):
    for _ in range(warmup):
        await fn()
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        await fn()
        lat.append((time.perf_counter() - t0) * 1000)
    return stats(lat)


def rss_mb():
    return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)


async def _new_page(rt, url=None):
    session = await rt.session_mgr.create(mode="ephemeral")
    ctx = rt.contexts[session.context_ids[0]]
    handle = await rt.page_mgr.open(ctx, url)
    return session, ctx, handle


async def run_suite(rt, base, n=30):
    be = backend()
    report = {"base": base, "iterations": n}

    # ---- 冷启动 ----
    if rt.default_handle is not None:
        await rt.processes.close(rt.default_handle)
        rt._default_handle = None
    t0 = time.perf_counter()
    await rt.ensure_default_browser()
    report["cold_start_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    session, ctx, h = await _new_page(rt, f"{base}/basic.html")

    # ---- 热连接 ----
    await h.actor.submit("warm", lambda: h.page.title())
    t0 = time.perf_counter()
    await h.actor.submit("hot", lambda: h.page.title())
    report["hot_ms"] = round((time.perf_counter() - t0) * 1000, 3)

    # ---- 操作延迟分布 ----
    report["latency"] = {}
    report["latency"]["read_title"] = await measure(
        lambda: h.actor.submit("title", lambda: h.page.title()), n)
    report["latency"]["eval_simple"] = await measure(
        lambda: h.actor.submit("eval", lambda: h.page.evaluate("document.title")), n)
    report["latency"]["locator_count"] = await measure(
        lambda: h.actor.submit("count", lambda: h.page.locator("#heading").count()), n)
    report["latency"]["click"] = await measure(
        lambda: h.actor.submit("click", lambda: be.act(
            h.page, {"by": "css", "value": "#btn"}, "click")), n)
    report["latency"]["fill"] = await measure(
        lambda: h.actor.submit("fill", lambda: be.act(
            h.page, {"by": "css", "value": "#text-input"}, "fill", value="x")), n)
    report["latency"]["snapshot_summary"] = await measure(
        lambda: h.actor.submit("snap", lambda: be.snapshot(h.page, level="summary")),
        min(n, 15))
    report["latency"]["explore"] = await measure(
        lambda: h.actor.submit("explore", lambda: be.explore(h.page)), min(n, 15))
    report["latency"]["navigate_local"] = await measure(
        lambda: h.actor.submit("nav", lambda: be.goto(h, f"{base}/basic.html")),
        min(n, 15))

    # ---- 同页串行吞吐 ----
    async def noop():
        return None
    t0 = time.perf_counter()
    await asyncio.gather(*[h.actor.submit("noop", noop) for _ in range(50)])
    report["same_page_50ops_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # ---- 跨页并行 ----
    _s2, ctx2, h2 = await _new_page(rt)

    async def slow(handle):
        async def fn():
            await asyncio.sleep(0.3)
        return await handle.actor.submit("slow", fn)

    t0 = time.perf_counter()
    await asyncio.gather(slow(h), slow(h2))
    parallel = time.perf_counter() - t0
    t0 = time.perf_counter()
    await slow(h)
    await slow(h2)
    serial = time.perf_counter() - t0
    report["cross_page_parallel_ms"] = round(parallel * 1000, 1)
    report["cross_page_serial_ms"] = round(serial * 1000, 1)
    report["cross_page_speedup"] = round(serial / parallel, 2) if parallel else None

    # ---- 多 session 并行 ----
    async def session_work():
        s = await rt.session_mgr.create(mode="ephemeral")
        c = rt.contexts[s.context_ids[0]]
        pg = await rt.page_mgr.open(c, f"{base}/basic.html")
        await pg.actor.submit("title", lambda: pg.page.title())
        await rt.session_mgr.close(s.session_id)
    t0 = time.perf_counter()
    await asyncio.gather(session_work(), session_work(), session_work())
    report["multi_session_parallel_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # ---- 资源泄漏（1000 次）----
    pages_before = len(rt.pages)
    contexts_before = len(rt.contexts)
    rss_before = rss_mb()
    for _ in range(1000):
        await h.actor.submit("noop", noop)
    report["leak"] = {
        "pages_before": pages_before, "pages_after": len(rt.pages),
        "contexts_before": contexts_before, "contexts_after": len(rt.contexts),
        "rss_before_mb": rss_before, "rss_after_mb": rss_mb(),
        "rss_growth_mb": round(rss_mb() - rss_before, 1),
    }

    # ---- 浏览器崩溃恢复 ----
    if rt.default_handle is not None:
        await rt.processes.close(rt.default_handle)
        rt._default_handle = None
    t0 = time.perf_counter()
    await rt.ensure_default_browser()
    report["crash_recovery_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # 收尾
    for s in list(rt.sessions.values()):
        try:
            await rt.session_mgr.close(s.session_id)
        except Exception:
            pass
    return report


def format_report(report):
    lines = []
    lines.append(f"base={report['base']}  iterations={report['iterations']}")
    lines.append(f"cold_start={report['cold_start_ms']}ms  "
                 f"hot={report['hot_ms']}ms  "
                 f"crash_recovery={report['crash_recovery_ms']}ms")
    lines.append("")
    lines.append(f"{'operation':22s} {'n':>4s} {'p50':>9s} {'p95':>9s} "
                 f"{'p99':>9s} {'mean':>9s} {'max':>9s}   (ms)")
    for op, s in report["latency"].items():
        lines.append(f"{op:22s} {s['n']:>4d} {s['p50']:>9.3f} {s['p95']:>9.3f} "
                     f"{s['p99']:>9.3f} {s['mean']:>9.3f} {s['max']:>9.3f}")
    lines.append("")
    lines.append(f"same_page_50ops={report['same_page_50ops_ms']}ms  "
                 f"cross_page parallel={report['cross_page_parallel_ms']}ms vs "
                 f"serial={report['cross_page_serial_ms']}ms "
                 f"(speedup x{report['cross_page_speedup']})")
    lines.append(f"multi_session_parallel(3x)={report['multi_session_parallel_ms']}ms")
    lk = report["leak"]
    lines.append(f"leak: pages {lk['pages_before']}->{lk['pages_after']}, "
                 f"contexts {lk['contexts_before']}->{lk['contexts_after']}, "
                 f"rss +{lk['rss_growth_mb']}MB "
                 f"({lk['rss_before_mb']}->{lk['rss_after_mb']})")
    return "\n".join(lines)
