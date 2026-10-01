"""成本与延迟指标（docs/tech-plan.md §5.11）。

每次 operation 记 cost = {connect_ms, locator_ms, action_ms, wait_ms,
assertion_ms, total_ms}；按 action_type 维护有界样本，输出 p50/p95/p99。
样本有上限，避免内存无界。
"""

import time
from collections import defaultdict, deque

_DEFAULT_MAXLEN = 1000


class MetricsRecorder:
    def __init__(self, maxlen: int = _DEFAULT_MAXLEN):
        self.maxlen = maxlen
        self._samples = defaultdict(lambda: deque(maxlen=maxlen))
        self._counts = defaultdict(int)

    def record(self, action_type: str, cost: dict):
        total = float(cost.get("total_ms", 0.0) or 0.0)
        self._samples[action_type].append(total)
        self._counts[action_type] += 1

    def record_elapsed(self, action_type: str, t0: float):
        """便捷：从 perf_counter 起点记一次 total_ms。"""
        self.record(action_type, {"total_ms": (time.perf_counter() - t0) * 1000})

    @staticmethod
    def _pct(sorted_vals, p):
        if not sorted_vals:
            return None
        idx = int(round((p / 100.0) * (len(sorted_vals) - 1)))
        idx = max(0, min(len(sorted_vals) - 1, idx))
        return round(sorted_vals[idx], 3)

    def snapshot(self):
        """返回 {action_type: {count, p50_ms, p95_ms, p99_ms, max_ms}}。"""
        out = {}
        for action_type, dq in self._samples.items():
            vals = sorted(dq)
            out[action_type] = {
                "count": self._counts[action_type],
                "window": len(vals),
                "p50_ms": self._pct(vals, 50),
                "p95_ms": self._pct(vals, 95),
                "p99_ms": self._pct(vals, 99),
                "max_ms": round(vals[-1], 3) if vals else None,
            }
        return out

    def reset(self):
        self._samples.clear()
        self._counts.clear()
