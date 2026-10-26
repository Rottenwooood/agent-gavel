"""Playwright 执行后端（docs/tech-plan.md §5.13）。

把 runtime 的语义操作翻译成 Playwright 调用；tools 层不直接碰 Playwright。
覆盖：生命周期/导航（M1）+ locator 解析/动作/断言/探索/读取（M2）。
"""

import asyncio
import base64

from ...core.errors import GavelError
from .locator import LocatorMixin
from .reader import ReaderMixin
from .util import sleep as _sleep

_TARGET_KEYS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")


# 作用域结构签名（地址/标题头 + 逐节点 tag#id.class[属性]+值/叶子文本）
class PlaywrightBackend(LocatorMixin, ReaderMixin):
    def __init__(self, runtime=None):
        self.runtime = runtime

    # ---- 导航竞态：识别 + 只读操作自动重试一次 ----
    _NAV_RACE = (
        "Execution context was destroyed",
        "Cannot find context",
        "Target page, context or browser has been closed",
        "navigating and changing the document",
        "frame was detached",
        "context was destroyed",
    )

    @classmethod
    def _is_nav_race(cls, e):
        s = str(e)
        return any(k in s for k in cls._NAV_RACE)

    async def retry_on_nav_race(self, handle, fn, retries=1):
        """只读操作遇导航竞态时，等页面稳定后自动重试一次；仍失败则报可重试错误。"""
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if not self._is_nav_race(e):
                raise
            if retries <= 0:
                raise GavelError(
                    "nav_race",
                    f"页面导航竞态：{e}",
                    retryable=True,
                    hint="页面正在导航，稍后重试读取",
                ) from e
            try:
                await handle.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                await _sleep(0.3)
            return await self.retry_on_nav_race(handle, fn, retries - 1)

    # ================= 导航 / 等待 =================
    async def goto(self, handle, url, *, wait_until="domcontentloaded", timeout_s=30.0):
        try:
            resp = await handle.page.goto(url, wait_until=wait_until, timeout=timeout_s * 1000)
        except Exception as e:  # noqa: BLE001
            if "Download is starting" in str(e):
                raise GavelError(
                    "download_started",
                    "导航触发了下载（不是页面）",
                    detail={"url": url},
                    retryable=False,
                    hint="这是下载链接：改用 page_wait_for_download 捕获下载，"
                    "或用 locator 点击后 page_wait_for_download",
                ) from e
            raise GavelError(
                "navigation_failed", f"导航失败：{e}", detail={"url": url}, retryable=True
            ) from e
        final = handle.page.url
        return {
            "requested_url": url,
            "final_url": final,
            "navigated": True,
            "redirected": final != url,
            "status_code": resp.status if resp is not None else None,
            "title": await handle.page.title(),
        }

    async def wait_load_state(self, handle, state="load", timeout_s=30.0):
        await handle.page.wait_for_load_state(state, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "state": state, "url": handle.page.url}

    async def reload(self, handle, *, wait_until="domcontentloaded", timeout_s=30.0):
        await handle.page.reload(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_back(self, handle, *, wait_until="domcontentloaded", timeout_s=30.0):
        await handle.page.go_back(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_forward(self, handle, *, wait_until="domcontentloaded", timeout_s=30.0):
        await handle.page.go_forward(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def wait_for_url(self, handle, pattern, timeout_s=30.0):
        await handle.page.wait_for_url(pattern, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def wait_for_selector(self, handle, selector, *, state="visible", timeout_s=30.0):
        await handle.page.wait_for_selector(selector, state=state, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "selector": selector, "state": state}

    async def wait_for_text(self, handle, text, *, state="visible", timeout_s=30.0, selector=None):
        """等某段文本出现（浏览器侧等待，满足即返回）。selector 给了就在其内部等。"""
        root = handle.page.locator(selector) if selector else handle.page
        await root.get_by_text(text, exact=False).first.wait_for(
            state=state, timeout=timeout_s * 1000
        )
        return {"page_id": handle.page_id, "text": text, "state": state, "url": handle.page.url}

    async def wait_for_response(self, handle, *, url=None, status=None, timeout_s=30.0):
        import fnmatch

        def match(resp):
            if status is not None and resp.status != status:
                return False
            if url:
                u = resp.url
                if "*" in url or "?" in url:
                    if not (fnmatch.fnmatch(u, url) or url in u):
                        return False
                elif url not in u:
                    return False
            return True

        resp = await handle.page.wait_for_event(
            "response", predicate=match, timeout=timeout_s * 1000
        )
        return {
            "page_id": handle.page_id,
            "url": resp.url,
            "http_status": resp.status,
            "method": resp.request.method,
        }

    # ================= dialog（不经 actor，避免与阻塞动作死锁）=================
    async def handle_dialog(self, handle, *, action="accept", prompt_text=None, policy=None):
        if policy is not None:
            if policy not in ("manual", "auto_accept", "auto_dismiss"):
                raise GavelError("bad_policy", f"未知 dialog policy：{policy}")
            handle.dialog_policy = policy
        dialog = handle.pending_dialog
        if dialog is None:
            if policy is not None:
                return {"page_id": handle.page_id, "policy": policy, "dialog": None}
            raise GavelError(
                "no_dialog",
                "当前没有待处理 dialog",
                detail={
                    "history": handle.dialog_history[-3:],
                    "hint": "dialog 可能已被 policy 自动处理",
                },
            )
        info = {"page_id": handle.page_id, "dialog_type": dialog.type, "message": dialog.message}
        if action == "accept":
            if dialog.type == "prompt" and prompt_text is not None:
                await dialog.accept(prompt_text)
            else:
                await dialog.accept()
            info["accepted"] = True
        elif action == "dismiss":
            await dialog.dismiss()
            info["accepted"] = False
        else:
            raise GavelError("bad_action", "action 取 accept|dismiss")
        handle.pending_dialog = None
        info["policy"] = handle.dialog_policy
        return info

    # ================= locator 解析 =================
    # ================= 动作 =================
    async def act(self, handle, target, action, *, timeout_s=30.0, **kw):
        from .actions import handler

        return await handler(action)(self, handle, target, timeout_s=timeout_s, **kw)

    async def pdf(self, handle, *, timeout_s=30.0):
        """无头用 Playwright；有头走 CDP Page.printToPDF（同一能力，不因模式缺失）。"""
        page = handle.page
        headless = handle.context.browser_handle.opts.get("headless")
        if headless:
            data = await page.pdf()
        else:
            cdp = await page.context.new_cdp_session(page)
            try:
                result = await asyncio.wait_for(
                    cdp.send(
                        "Page.printToPDF",
                        {
                            "printBackground": True,
                            "preferCSSPageSize": True,
                        },
                    ),
                    timeout=timeout_s,
                )
            finally:
                try:
                    await cdp.detach()
                except Exception:
                    pass  # silent-ok: best-effort，失败不影响主流程
            data = base64.b64decode(result["data"])
        return {"data": data, "url": page.url}

    # ================= 断言 =================
    async def page_signature(self, handle, target=None):
        from .signature import page_signature

        return await page_signature(self, handle, target)

    @staticmethod
    def diff_summary(before, after):
        from .signature import diff_summary

        return diff_summary(before, after)

    async def evaluate_checks(self, handle, checks, before_sig=None, wait_s=0.0, target=None):
        from .assertion import evaluate

        return await evaluate(
            self, handle, checks, before_sig=before_sig, wait_s=wait_s, target=target
        )
