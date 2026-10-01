"""性能门槛（docs/tech-plan.md §12）——真实测量并断言，不通过即失败。

会打印完整 p50/p95/p99 表格（-s 可见）。
"""

import benchlib


async def test_performance_gates(runtime, webapp_server, capsys):
    report = await benchlib.run_suite(runtime, webapp_server, n=20)
    with capsys.disabled():
        print("\n" + benchlib.format_report(report))

    lat = report["latency"]
    # 启动 / 连接 / 恢复
    assert report["cold_start_ms"] < 1500, report["cold_start_ms"]
    assert report["hot_ms"] < 20, report["hot_ms"]
    assert report["crash_recovery_ms"] < 5000, report["crash_recovery_ms"]
    # 本地简单读取 p95 < 50ms；动作 p95 < 100ms
    assert lat["read_title"]["p95"] < 50, lat["read_title"]
    assert lat["eval_simple"]["p95"] < 50, lat["eval_simple"]
    assert lat["click"]["p95"] < 100, lat["click"]
    assert lat["fill"]["p95"] < 100, lat["fill"]
    # 跨页并发确实并行
    assert report["cross_page_speedup"] and report["cross_page_speedup"] > 1.5, \
        report["cross_page_speedup"]
    # 1000 次操作不泄漏 page/context；RSS 增长受控
    lk = report["leak"]
    assert lk["pages_after"] == lk["pages_before"], lk
    assert lk["contexts_after"] == lk["contexts_before"], lk
    assert lk["rss_growth_mb"] < 150, lk
