"""动作前后的状态差分（observation）。

一次动作的副作用分布在四层：context（新页/下载/active page）、page 宿主
（url/标题/dialog/异常/失败请求）、交互态（焦点与表单值）、DOM。

本模块是这四层观测的唯一主人。契约：**只输出变化项，没有变化就是空 dict**；
key 不存在即表示"没变"，不需要用 `unchanged` 占位。

`pages.opened` 漏检的根因是时序，不是字段缺失：
- 新页面由 `context.on("page")` 异步登记（runtime/contexts.py），动作返回时
  往往还没落地；
- 而且 `browser_context.pages` 在页面刚创建时也未必立刻可见。
所以不能"比较注册表再看有没有 pending"——必须在动作期间就挂上自己的
`page` 监听，看见新页再等它登记。下载同理：`download_started` 是同步事件，
产物落盘是异步的。
"""

import asyncio
import time

from .signature import diff_summary, page_signature

_SETTLE_CAP_S = 2.5
_SETTLE_STEP_S = 0.02
# 锚点点击才需要"等迟到副作用"：下载事件在 click 返回后才到。
# 按钮/输入类动作不等，避免给每次点击加固定延迟（性能门禁 click p95 < 100ms）。
_GRACE_S = 0.15
# 只有"可能开新页/触发下载"的锚点才需要长窗口：_blank 弹窗、download 下载。
# 实测有头默认容器下弹窗登记约 0.2–0.5s，故取 0.6s；普通站内跳转只给 0.15s。
_GRACE_SLOW = 1.2
_MAX_SAMPLES = 3
_MAX_ERRORS = 5
_MAX_FAILED = 5

_FOCUS_JS = """
() => {
  const e = document.activeElement;
  if (!e || e === document.body) return null;
  const tag = e.tagName.toLowerCase();
  let value = null;
  if (tag === 'input' || tag === 'textarea') {
    value = (e.getAttribute('type') === 'password') ? '***' : String(e.value).slice(0, 120);
  } else if (tag === 'select') {
    const o = e.selectedOptions && e.selectedOptions[0];
    value = o ? String(o.text).slice(0, 120) : null;
  }
  return {
    tag: tag,
    id: e.id || null,
    name: e.getAttribute('name') || null,
    type: e.getAttribute('type') || null,
    role: e.getAttribute('role') || null,
    value: value,
  };
}
"""


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


async def _safe_await(fn):
    try:
        return await fn()
    except Exception:
        return None


def _label(el):
    """焦点元素的可读标识：input#codeValue / a[name=q]。"""
    if not el:
        return None
    tag = el.get("tag") or "?"
    if el.get("id"):
        return f"{tag}#{el['id']}"
    if el.get("name"):
        return f"{tag}[name={el['name']}]"
    return tag


class Observation:
    """`async with Observation(backend, handle, target=...) as obs: ...`

    退出时 `obs.diff` 是变化项；为空表示这一步什么都没改变。
    """

    _GRACE_ACTIONS = frozenset({"click", "press"})

    def __init__(self, backend, handle, *, target=None, action=None, follow_popup=True):
        self.backend = backend
        self._follow_popup = follow_popup
        self.handle = handle
        self.target = target
        self.action = action
        self.rt = getattr(backend, "runtime", None)
        self.diff = {}
        self._t0 = 0.0
        self._before = {}
        self._seen_pages = []
        self._page_handler = None

    async def __aenter__(self):
        try:
            self._t0 = time.time()
            self._attach_page_listener()
            self._grace = await self._compute_grace()
            page = self.handle.page
            self._before = {
                "pages": set(self.rt.pages) if self.rt else set(),
                "active": self.handle.context.active_page_id,
                "arts": {a["artifact_id"] for a in self.rt.artifacts.list()} if self.rt else set(),
                "url": _safe(lambda: page.url),
                "title": await _safe_await(page.title),
                "focus": await _safe_await(lambda: page.evaluate(_FOCUS_JS)),
                "sig": await page_signature(self.backend, self.handle, target=self.target),
            }
        except Exception:
            self._before = {}
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._detach_page_listener()
        if exc_type is not None:
            # 动作失败时不猜状态；错误本身由调用方处理
            return False
        try:
            await self._settle()
            self.diff = await self._collect()
        except Exception:
            self.diff = {}
        return False

    # ---- 动作期间的同步监听（这是能看见新页的前提）----
    def _attach_page_listener(self):
        try:
            bc = self.handle.context.browser_context
        except Exception:
            return

        def _on_page(pw_page):
            self._seen_pages.append(pw_page)

        try:
            bc.on("page", _on_page)
            self._page_handler = _on_page
        except Exception:
            self._page_handler = None

    def _detach_page_listener(self):
        handler = self._page_handler
        self._page_handler = None
        if handler is None:
            return
        try:
            self.handle.context.browser_context.remove_listener("page", handler)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程

    async def _compute_grace(self):
        """只有"点链接"才可能触发浏览器原生下载/弹窗，才值得等迟到事件。"""
        if self.action not in self._GRACE_ACTIONS or not self.target:
            return 0.0
        try:
            loc = (await self.backend.resolve(self.handle, self.target)).first
            info = await loc.evaluate(
                "el => {const a = el.closest && el.closest('a'); if (!a) return null;"
                " return {download: a.hasAttribute('download'),"
                " blank: (a.getAttribute('target') || '').toLowerCase() === '_blank'};}"
            )
        except Exception:
            return 0.0
        if not info:
            return 0.0
        return _GRACE_SLOW if (info.get("download") or info.get("blank")) else _GRACE_S

    # ---- 等异步登记落地 ----
    async def _settle(self):
        rt = self.rt
        if rt is None:
            return
        # 下载/弹窗事件可能在 click 返回之后才到达：先给一个很短的静默窗口，
        # 一旦看见信号就等它落地，落地后立刻返回（不额外拖时间）。
        grace = getattr(self, "_grace", 0.0)
        deadline = time.monotonic() + _SETTLE_CAP_S
        quiet_since = time.monotonic()
        saw_signal = False
        while time.monotonic() < deadline:
            if self._pending():
                saw_signal = True
                quiet_since = time.monotonic()
                await asyncio.sleep(_SETTLE_STEP_S)
                continue
            if saw_signal:
                return
            if time.monotonic() - quiet_since >= grace:
                return
            await asyncio.sleep(_SETTLE_STEP_S)

    def _pending(self):
        rt = self.rt
        # 1) 看见新页，但还没登记进注册表。
        #    同时看 browser_context.pages：有头默认容器下 page 事件可能晚于
        #    动作返回，注册表也晚，两条线索都要用。
        try:
            live_pages = list(self.handle.context.browser_context.pages)
        except Exception:
            live_pages = []
        for pw_page in list(self._seen_pages) + live_pages:
            if rt.page_mgr.find_by_pw_page(pw_page) is None:
                return True
        # 2) 下载已触发，但产物还没落盘
        events = rt.events.recent(limit=200)
        started = [
            e for e in events if e.get("event") == "download_started" and e.get("ts", 0) >= self._t0
        ]
        if started:
            done = [a for a in rt.artifacts.list() if a.get("created_at", 0) >= self._t0]
            if len(done) < len(started):
                return True
        return False

    # ---- 采集 ----
    async def _collect(self):
        out = {}
        if self.rt is not None:
            self._collect_context(self.rt, out)
        await self._collect_page(out)
        return out

    def _collect_context(self, rt, out):
        before = self._before
        now_pages = dict(rt.pages)
        opened = [
            {"page_id": pid, "opener_page_id": ph.opener_page_id, "url": ph._safe_url()}
            for pid, ph in now_pages.items()
            if pid not in before.get("pages", set())
        ]
        closed = [pid for pid in before.get("pages", set()) if pid not in now_pages]
        # 点击/按键弹出的新页：opener 是当前页就跟随，避免"眼见在新页、动作落在老页"
        if (
            opened
            and self.action in ("click", "press")
            and self._follow_popup
            and self.handle.context is not None
        ):
            for item in opened:
                if item.get("opener_page_id") == self.handle.page_id:
                    self.handle.context.active_page_id = item["page_id"]
                    out["switched_to_new_tab"] = True
                    break
        active = self.handle.context.active_page_id
        pages = {}
        if opened:
            pages["opened"] = opened
        if closed:
            pages["closed"] = closed
        if active != before.get("active"):
            pages["active"] = {"from": before.get("active"), "to": active}
        if pages:
            out["pages"] = pages

        arts_now = {a["artifact_id"]: a for a in rt.artifacts.list()}
        new_arts = [
            {
                k: a.get(k)
                for k in ("artifact_id", "kind", "suggested_filename", "size_bytes")
                if a.get(k) is not None
            }
            for aid, a in arts_now.items()
            if aid not in before.get("arts", set())
        ]
        if new_arts:
            out["artifacts"] = new_arts

        events = [
            e
            for e in rt.events.recent(limit=500)
            if e.get("ts", 0) >= self._t0 and e.get("context_id") == self.handle.context_id
        ]
        dialogs = [
            {"type": e.get("dialog_type"), "message": e.get("message")}
            for e in events
            if e.get("event") == "dialog_opened"
        ]
        if dialogs:
            out["dialogs"] = dialogs

        errors = [
            e.get("text") or e.get("error")
            for e in events
            if e.get("event") in ("page_error", "console_error")
            # 静态子资源的加载失败对正文无影响，且没有 URL 无法判断，直接丢弃
            and "Failed to load resource" not in str(e.get("text") or "")
        ]
        if errors:
            out["errors"] = errors[:_MAX_ERRORS]
            if len(errors) > _MAX_ERRORS:
                out["errors_omitted"] = len(errors) - _MAX_ERRORS

        # 只保留主文档与接口层的失败；图片/字体/样式的 404 不进 diff
        http_errors = [
            {
                "status": e.get("status"),
                "type": e.get("resource_type"),
                "url": e.get("url"),
            }
            for e in events
            if e.get("event") == "response"
            and isinstance(e.get("status"), int)
            and e["status"] >= 400
            and e.get("resource_type") in ("document", "xhr", "fetch")
        ]
        if http_errors:
            out.setdefault("network", {})["errors"] = http_errors[:_MAX_FAILED]

        failed = [
            {"url": e.get("url"), "error": e.get("error")}
            for e in events
            if e.get("event") == "request_failed"
        ]
        if failed:
            out.setdefault("network", {})["failed"] = failed[:_MAX_FAILED]
            if len(failed) > _MAX_FAILED:
                out["network"]["failed_omitted"] = len(failed) - _MAX_FAILED

        blocked = [e for e in events if e.get("event") == "policy_blocked"]
        if blocked:
            out.setdefault("network", {})["policy_blocked"] = len(blocked)

    async def _collect_page(self, out):
        before = self._before
        page = self.handle.page

        url = _safe(lambda: page.url)
        if url != before.get("url"):
            out["url"] = {"from": before.get("url"), "to": url}

        title = await _safe_await(page.title)
        if title is not None and title != before.get("title"):
            out["title"] = {"from": before.get("title"), "to": title}

        focus = await _safe_await(lambda: page.evaluate(_FOCUS_JS))
        if focus != before.get("focus"):
            out["focus"] = {"from": _label(before.get("focus")), "to": _label(focus)}
            old_v = (before.get("focus") or {}).get("value")
            new_v = (focus or {}).get("value")
            if old_v != new_v and (old_v is not None or new_v is not None):
                out["values"] = [
                    {
                        "selector": _label(focus or before.get("focus")),
                        "from": old_v,
                        "to": new_v,
                    }
                ]

        await self._collect_dom(out)

    async def _collect_dom(self, out):
        sig_before = self._before.get("sig")
        if not (isinstance(sig_before, dict) and sig_before.get("lines")):
            return
        sig_after = await _safe_await(
            lambda: page_signature(self.backend, self.handle, target=self.target)
        )
        if not (isinstance(sig_after, dict) and sig_after.get("lines")):
            return
        d = diff_summary(sig_before, sig_after)
        if not d.get("changed"):
            return
        dom = {
            "added": d.get("added", 0),
            "removed": d.get("removed", 0),
            "meaningful": bool(d.get("meaningful")),
            "scope": d.get("scope"),
        }
        if d.get("samples_added"):
            dom["samples_added"] = d["samples_added"][:_MAX_SAMPLES]
        if d.get("samples_removed"):
            dom["samples_removed"] = d["samples_removed"][:_MAX_SAMPLES]
        out["dom"] = dom
