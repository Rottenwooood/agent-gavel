"""agent-gavel DOM 验证分支矩阵测试（直连）。

覆盖 verify.py 的 pass / fail / ambiguous 各分支，并计时。用法:
  cd agent-gavel && uv run python3 tests/verify_branches.py

分支矩阵（strict=True，避免降级掩盖单次行为）:
  A 有断言 + 满足                  -> pass      (mode=feature)
  B 有断言 + 不满足 + 页面有变化    -> ambiguous (feature->diff(early), assertion_mismatch_but_change)
  C 有断言 + 不满足 + 页面无变化    -> fail      (assertion_timeout)
  D 无断言 + 页面有变化            -> pass      (mode=diff)
  E 无断言 + 页面无变化            -> ambiguous (no_observable_change)
  F 无断言 + diff 不可用(scroll)   -> pass      (mode=feature, 盲放行边界)
  G 有断言 + 满足 (wait_mode=event) -> pass      (mode=event)
  H 有断言 + 延迟 500ms 满足(poll)  -> pass      (mode=feature, 多轮轮询)
  I 有断言 + 无变化 (strict=False)  -> fail      (降级全失败 all_strategies_failed)
  J navigate + 断言               -> pass      (mode=feature)

fixture: tests/fixtures/verify_branches.html
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gavel.channels.dom.adapter import DomClient
from agent_gavel.channels.dom.verify import dom_act_and_verify

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "verify_branches.html")
URL = f"file://{FIXTURE}"

TXT = {"txt": "document.querySelector('#result').textContent"}
TITLE = {"title": "document.title"}
WANT_DONE = {"txt": {"op": "eq", "value": "DONE"}}
WANT_TITLE = {"title": {"op": "eq", "value": "verify branches"}}

# (name, kwargs, want_status, want_substr)
CASES = [
    ("A 有断言+满足",
     dict(action="click", selectors={"target": "#btn-assert"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=2.0, strict=True),
     "pass", "feature"),
    ("B 有断言+不满足+有变化",
     dict(action="click", selectors={"target": "#btn-change"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=2.0, strict=True),
     "ambiguous", "assertion_mismatch_but_change"),
    ("C 有断言+不满足+无变化",
     dict(action="click", selectors={"target": "#btn-noop"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=1.0, strict=True),
     "fail", "assertion_timeout"),
    ("D 无断言+有变化",
     dict(action="click", selectors={"target": "#btn-change"},
          wait_s=2.0, strict=True),
     "pass", "diff"),
    ("E 无断言+无变化",
     dict(action="click", selectors={"target": "#btn-noop"},
          wait_s=2.0, strict=True),
     "ambiguous", "no_observable_change"),
    ("F 无断言+diff不可用(scroll)",
     dict(action="scroll", selectors={"direction": "down"},
          wait_s=1.0, strict=True),
     "pass", "feature"),
    ("G 有断言+满足(event)",
     dict(action="click", selectors={"target": "#btn-assert"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=2.0,
          wait_mode="event", strict=True),
     "pass", "event"),
    ("H 有断言+延迟满足(poll)",
     dict(action="click", selectors={"target": "#btn-delay"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=3.0, strict=True),
     "pass", "feature"),
    ("I 降级:有断言+无变化(strict=False)",
     dict(action="click", selectors={"target": "#btn-noop"},
          page_features=TXT, expected_feature=WANT_DONE, wait_s=0.5, strict=False),
     "fail", "all_strategies_failed"),
    ("J navigate+断言",
     dict(action="navigate", selectors={"url": URL},
          page_features=TITLE, expected_feature=WANT_TITLE, wait_s=2.0, strict=True),
     "pass", "feature"),
]


async def main():
    all_ok = True
    rows = []
    async with DomClient() as client:
        for name, kw, want_st, want_sub in CASES:
            # 每用例前重载 fixture，重置页面状态
            await client.navigate(URL)
            await client.wait_page_load()
            t0 = time.perf_counter()
            r = await dom_act_and_verify(client, log_prefix="vb", **kw)
            ms = int((time.perf_counter() - t0) * 1000)
            st = r.get("status")
            ver = r.get("verification", {})
            mode = ver.get("mode", "")
            reason = r.get("reason", "")
            blob = f"{mode} {reason}"
            ok = (st == want_st) and (want_sub in blob)
            if not ok:
                all_ok = False
            retries = r.get("retries")
            rt = ""
            if retries:
                rt = " retries=" + ",".join(
                    f"{x['strategy']}:{x['status']}" for x in retries)
            rows.append((name, st, want_st, mode, reason, ms, ok, rt))
            print(f"[{'OK ' if ok else 'BAD'}] {name:<34} "
                  f"status={st:<9} want={want_st:<9} {ms:>5}ms  "
                  f"mode={mode} reason={reason}{rt}", flush=True)
    print()
    print(f"{'用例':<36}{'状态':<11}{'耗时ms':>7}  判定")
    for name, st, want, mode, reason, ms, ok, rt in rows:
        print(f"{name:<36}{st:<11}{ms:>7}  {'OK' if ok else 'BAD'}")
    print("\n" + ("ALL PASS" if all_ok else "HAS FAIL"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
