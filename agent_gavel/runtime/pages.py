"""Page 资源（docs/tech-plan.md §5.6）。

稳定 page_id、opener 关系、事件绑定（close/crash/pageerror/console，下载与
dialog 在 M3 补全）。每个 page 带一个 PageActor 保证动作串行。
"""

import asyncio

from .errors import GavelError
from .queue import PageActor


class PageHandle:
    def __init__(self, page_id, context, page, runtime, opener_page_id=None):
        self.page_id = page_id
        self.context = context
        self.page = page
        self.runtime = runtime
        self.opener_page_id = opener_page_id
        self.status = "open"
        self.actor = PageActor(page_id, metrics=runtime.metrics)

    @property
    def session_id(self):
        return self.context.session.session_id

    @property
    def context_id(self):
        return self.context.context_id

    def to_dict(self):
        info = {
            "page_id": self.page_id,
            "context_id": self.context_id,
            "session_id": self.session_id,
            "url": self._safe_url(),
            "opener_page_id": self.opener_page_id,
            "page_status": self.status,
        }
        try:
            info["closed"] = self.page.is_closed()
        except Exception:
            info["closed"] = self.status == "closed"
        return info

    def _safe_url(self):
        try:
            return self.page.url
        except Exception:
            return None

    def _emit(self, event, **kw):
        payload = {"event": event, "session_id": self.session_id,
                   "context_id": self.context_id, "page_id": self.page_id}
        payload.update(kw)
        self.runtime.events.emit(payload)

    # ---- 事件绑定 ----
    def bind(self):
        p = self.page
        p.on("close", lambda: self._on_close())
        p.on("crash", lambda: self._on_crash())
        p.on("pageerror", lambda exc: self._emit("page_error", error=str(exc)))
        p.on("console", self._on_console)
        p.on("framenavigated", self._on_navigated)

    def _on_console(self, msg):
        try:
            if msg.type in ("error", "warning"):
                self._emit("console_error" if msg.type == "error" else "console_warning",
                           text=msg.text)
        except Exception:
            pass

    def _on_navigated(self, frame):
        try:
            if frame == self.page.main_frame:
                self._emit("navigated", url=frame.url)
        except Exception:
            pass

    def _on_close(self):
        if self.status == "closed":
            return
        self.status = "closed"
        self._emit("page_closed", url=self._safe_url())
        self.runtime.schedule(self.runtime.page_mgr.close(self.page_id,
                                                       already_closed=True))

    def _on_crash(self):
        self.status = "crashed"
        self._emit("page_crashed", url=self._safe_url())


class PageManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self._reg_lock = asyncio.Lock()
        self._by_key = {}  # id(pw_page) -> PageHandle

    def get(self, page_id):
        ph = self.runtime.pages.get(page_id)
        if ph is None:
            raise GavelError("page_not_found", f"未知 page：{page_id}",
                             hint="用 page_open 打开，或 page_list 查看")
        return ph

    def resolve(self, page_id=None):
        """省略 page_id 时用 runtime.active_page_id，其次唯一 page。"""
        if page_id:
            return self.get(page_id)
        if self.runtime.active_page_id:
            ph = self.runtime.pages.get(self.runtime.active_page_id)
            if ph is not None and ph.status != "closed":
                return ph
        alive = [p for p in self.runtime.pages.values() if p.status != "closed"]
        if len(alive) == 1:
            return alive[0]
        if not alive:
            raise GavelError("no_page", "当前没有打开的 page",
                             hint="先 page_open")
        raise GavelError("ambiguous_page", "有多个 page，请显式指定 page_id",
                         detail={"pages": [p.page_id for p in alive]})

    async def register(self, ctx, pw_page, opener_page_id=None):
        async with self._reg_lock:
            key = id(pw_page)
            existing = self._by_key.get(key)
            if existing is not None:
                return existing
            pid = self.runtime.next_id("page")
            handle = PageHandle(pid, ctx, pw_page, self.runtime,
                                opener_page_id=opener_page_id)
            self.runtime.pages[pid] = handle
            self._by_key[key] = handle
            ctx.page_ids.append(pid)
            if ctx.active_page_id is None:
                ctx.active_page_id = pid
            self.runtime.active_page_id = pid
            handle.bind()
            await handle.actor.start()
            handle._emit("page_created", url=handle._safe_url())
            return handle

    async def open(self, ctx, url=None):
        pw_page = await ctx.browser_context.new_page()
        handle = await self.register(ctx, pw_page)
        if url:
            self.runtime.policies.check_domain(url)
            await handle.actor.submit(
                "navigate",
                lambda: pw_page.goto(url, wait_until="domcontentloaded",
                                     timeout=30000))
        return handle

    async def close(self, page_id, *, already_closed=False):
        handle = self.runtime.pages.pop(page_id, None)
        if handle is None:
            return {"page_id": page_id, "closed": True}
        self._by_key.pop(id(handle.page), None)
        await handle.actor.close("page_closed")
        if not already_closed:
            try:
                await handle.page.close()
            except Exception:
                pass
        handle.status = "closed"
        ctx = handle.context
        if page_id in ctx.page_ids:
            ctx.page_ids.remove(page_id)
        if ctx.active_page_id == page_id:
            ctx.active_page_id = ctx.page_ids[-1] if ctx.page_ids else None
        if self.runtime.active_page_id == page_id:
            self.runtime.active_page_id = ctx.active_page_id
        return {"page_id": page_id, "closed": True}
