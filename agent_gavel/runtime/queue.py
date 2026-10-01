"""每 page 的 actor 命令队列（docs/tech-plan.md §5.7）。

同一个 page 的所有操作进同一队列 → 严格串行；
不同 page 是不同 actor → 真并行。支持 priority / timeout / deadline /
cancel / operation_id，完成后写 metrics。

取代旧的全局 PAGE_LOCK：串行粒度收窄到单 page。
"""

import asyncio
import itertools
import time
import uuid

from .errors import GavelError, wrap_exception


class Operation:
    __slots__ = ("operation_id", "action_type", "fn", "priority", "timeout_s",
                 "deadline", "cancel_event", "created_at", "cancelled", "fut")

    def __init__(self, operation_id, action_type, fn, priority, timeout_s,
                 deadline, cancel_event):
        self.operation_id = operation_id
        self.action_type = action_type
        self.fn = fn
        self.priority = priority
        self.timeout_s = timeout_s
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.created_at = time.monotonic()
        self.cancelled = False
        self.fut = None


class PageActor:
    def __init__(self, page_id: str, *, metrics=None):
        self.page_id = page_id
        self.metrics = metrics
        self._pq = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._ops = {}            # operation_id -> Operation（含当前执行中）
        self._current = None
        self._current_task = None
        self._worker = None
        self._closed = False

    # ---- 生命周期 ----
    async def start(self):
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    @property
    def pending(self) -> int:
        return len(self._ops)

    @property
    def idle(self) -> bool:
        return self._current is None and self._pq.empty()

    async def close(self, reason: str = "page_closed"):
        """停止队列：拒绝所有排队/在途操作，结束 worker。"""
        if self._closed:
            return
        self._closed = True
        err = GavelError(reason, f"page {self.page_id} 已关闭")
        for op in list(self._ops.values()):
            if op.fut is not None and not op.fut.done():
                op.fut.set_exception(err)
        self._ops.clear()
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker = None

    # ---- 提交 ----
    async def submit(self, action_type, fn, *, priority: int = 100,
                     timeout_s: float = None, deadline: float = None,
                     operation_id: str = None):
        if self._closed:
            raise GavelError("page_closed", f"page {self.page_id} 已关闭")
        loop = asyncio.get_running_loop()
        op = Operation(
            operation_id or f"op_{uuid.uuid4().hex[:8]}",
            action_type, fn, priority, timeout_s, deadline,
            asyncio.Event(),
        )
        op.fut = loop.create_future()
        self._ops[op.operation_id] = op
        await self._pq.put((priority, next(self._seq), op))
        return await op.fut

    def cancel(self, operation_id: str) -> bool:
        op = self._ops.get(operation_id)
        if op is None:
            return False
        op.cancelled = True
        op.cancel_event.set()
        if self._current is op and self._current_task is not None \
                and not self._current_task.done():
            self._current_task.cancel()
        return True

    # ---- 执行循环 ----
    async def _run(self):
        while True:
            if self._closed:
                break
            try:
                _, _, op = await self._pq.get()
            except asyncio.CancelledError:
                break
            if op.fut.done():
                self._ops.pop(op.operation_id, None)
                continue
            if op.cancelled:
                self._fail(op, GavelError("cancelled", "操作已取消", retryable=True))
                continue
            self._current = op
            self._current_task = asyncio.ensure_future(op.fn())
            t0 = time.perf_counter()
            try:
                eff = self._effective_timeout(op)
                if eff is not None and eff <= 0:
                    raise GavelError("timeout", "操作超时（deadline 已过）",
                                     retryable=True)
                if eff is None:
                    result = await self._current_task
                else:
                    result = await asyncio.wait_for(self._current_task, eff)
                if not op.fut.done():
                    op.fut.set_result(result)
            except asyncio.TimeoutError:
                self._current_task.cancel()
                self._fail(op, GavelError(
                    "timeout", f"操作超时（{op.timeout_s or 'deadline'}s）",
                    retryable=True))
            except asyncio.CancelledError:
                self._fail(op, GavelError("cancelled", "操作已取消", retryable=True))
                # 若 closed，下一轮循环顶部 break；否则继续取下一个操作
            except GavelError as e:
                self._fail(op, e)
            except Exception as e:  # noqa: BLE001
                self._fail(op, wrap_exception(e))
            finally:
                if self.metrics is not None:
                    self.metrics.record_elapsed(op.action_type, t0)
                self._current = None
                self._current_task = None
                self._ops.pop(op.operation_id, None)

    def _effective_timeout(self, op: Operation):
        eff = op.timeout_s
        if op.deadline is not None:
            rem = op.deadline - time.monotonic()
            eff = rem if eff is None else min(eff, rem)
        return eff

    @staticmethod
    def _fail(op: Operation, err: GavelError):
        if op.fut is not None and not op.fut.done():
            op.fut.set_exception(err)
