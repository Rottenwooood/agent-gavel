"""诊断设施：Playwright trace + 网络记录（docs/tech-plan.md §5.12）。

- Tracer：按 context 开/关 Playwright tracing，stop 产出 zip（存 artifact）
- NetworkRecorder：按 context 开启后，page 的 request/response/requestfailed
  事件写入有界缓冲，stop 时返回
"""

import time


class Tracer:
    def __init__(self):
        self.active = set()

    def is_active(self, context_id):
        return context_id in self.active

    async def start(self, context):
        await context.browser_context.tracing.start(
            screenshots=True, snapshots=True, sources=True)
        self.active.add(context.context_id)

    async def stop(self, context, path):
        await context.browser_context.tracing.stop(path=path)
        self.active.discard(context.context_id)


class NetworkRecorder:
    def __init__(self, maxlen=2000):
        self.maxlen = maxlen
        self._enabled = set()
        self._records = {}

    def is_active(self, context_id):
        return context_id in self._enabled

    def start(self, context_id):
        self._enabled.add(context_id)
        self._records.setdefault(context_id, [])
        return {"context_id": context_id, "enabled": True}

    def stop(self, context_id, limit=1000):
        items = list(self._records.get(context_id, []))[-limit:]
        self._enabled.discard(context_id)
        return {"context_id": context_id, "enabled": False,
                "records": items, "count": len(items)}

    def record(self, context_id, entry):
        if context_id not in self._enabled:
            return
        buf = self._records.setdefault(context_id, [])
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        buf.append(entry)
        if len(buf) > self.maxlen:
            del buf[:len(buf) - self.maxlen]
