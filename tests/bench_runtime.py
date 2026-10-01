"""独立性能基准报告（docs/tech-plan.md §12）。

用法：AGENT_GAVEL_HEADLESS=1 uv run python tests/bench_runtime.py [iterations]
产出：终端表格 + logs/bench-<ts>.json
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchlib import format_report, run_suite  # noqa: E402
from webapp_server import start_server  # noqa: E402

from agent_gavel.runtime import reset_runtime, shutdown_runtime  # noqa: E402


async def main(n=30):
    httpd, base = start_server()
    rt = reset_runtime()
    try:
        report = await run_suite(rt, base, n=n)
    finally:
        await shutdown_runtime()
        httpd.shutdown()
    print(format_report(report))
    os.makedirs("logs", exist_ok=True)
    out = os.path.join("logs", f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {out}")
    return report


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    asyncio.run(main(n))
