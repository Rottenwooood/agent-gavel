"""agent-gavel DOM 动作空间全覆盖测试（直连）。

跑 tests/atomic_coverage.json（17 步，覆盖全部 11 个动作类型），
在本地 fixture 页上逐步验证。用法:
  cd agent-gavel && uv run python3 tests/dom_atomic_coverage.py
全过打印 ALL PASS。

fixture: tests/fixtures/atomic_coverage.html（本地页，含输入框/按钮/右键/
hover/滚动区/拖拽源+放置区/键盘日志）
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gavel.channels.dom.adapter import DomClient
from agent_gavel.channels.dom.verify import dom_act_and_verify
from agent_gavel.channels.dom.templates import _fill

HERE = os.path.dirname(os.path.abspath(__file__))
TMPL = os.path.join(HERE, "atomic_coverage.json")
FIXTURE = os.path.join(HERE, "fixtures", "atomic_coverage.html")


async def main():
    with open(TMPL, encoding="utf-8") as f:
        tmpl = json.load(f)
    filled = _fill(tmpl, {"FILE": f"file://{FIXTURE}"})
    all_ok = True
    async with DomClient() as client:
        for i, step in enumerate(filled["steps"]):
            t0 = time.perf_counter()
            r = await dom_act_and_verify(
                client, action=step["action"],
                selectors=step.get("selectors"),
                page_features=step.get("page_features"),
                expected_feature=step.get("expected_feature"),
                wait_s=8, log_prefix=f"cov_s{i}")
            ms = int((time.perf_counter() - t0) * 1000)
            st = r["status"]
            if st != "pass":
                all_ok = False
            detail = ""
            if st != "pass" and r.get("verification", {}).get("checks"):
                chk = r["verification"]["checks"]
                detail = " | " + " ".join(
                    f"{c['feature']}={c['actual']!r}(want {c['expected']!r})"
                    for c in chk[:2])
            print(f"[{'PASS' if st=='pass' else 'FAIL'}] step{i:2d} "
                  f"{step['action']:<12} {ms:>5}ms{detail}", flush=True)
    print("\n" + ("ALL PASS" if all_ok else "HAS FAIL"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
