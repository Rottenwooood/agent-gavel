"""事件总线（docs/tech-plan.md §5.8）。

生产者是 page/context 的 Playwright 事件处理器；消费面有两个：
① 有界环形缓冲，供 network_*/console_get 拉取；
② wait_for 一次性等待（page_wait_for_event/wait_for_popup 等）。
缓冲区满丢弃最旧并计数，不阻塞生产者。
"""

import asyncio
import time
from collections import deque


class EventBus:
    def __init__(self, maxlen: int = 500):
        self._buf = deque(maxlen=maxlen)
        self._dropped = 0
        self._waiters = []  # list[(predicate, future)]

    def emit(self, event: dict):
        ev = dict(event)
        ev.setdefault("ts", time.time())
        if len(self._buf) == self._buf.maxlen:
            self._dropped += 1
        self._buf.append(ev)
        if self._waiters:
            remaining = []
            for pred, fut in self._waiters:
                if fut.done():
                    continue
                try:
                    if pred(ev):
                        fut.set_result(ev)
                        continue
                except Exception:
                    pass
                remaining.append((pred, fut))
            self._waiters = remaining
        return ev

    def recent(self, kinds=None, limit: int = 100):
        items = [e for e in self._buf
                 if not kinds or e.get("event") in kinds]
        return items[-limit:]

    def stats(self):
        return {"buffered": len(self._buf), "dropped": self._dropped,
                "waiters": len(self._waiters)}

    async def wait_for(self, predicate, timeout: float):
        """等待第一个满足 predicate 的事件；超时返回 None。"""
        for e in reversed(self._buf):
            try:
                if predicate(e):
                    return e
            except Exception:
                pass
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._waiters.append((predicate, fut))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._waiters = [(p, f) for (p, f) in self._waiters if f is not fut]
            return None

    def clear(self):
        self._buf.clear()
        self._dropped = 0
