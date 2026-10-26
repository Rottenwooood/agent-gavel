"""Playwright 执行后端（docs/tech-plan.md §5.13）。

把 runtime 的语义操作翻译成 Playwright 调用；tools 层不直接碰 Playwright。
覆盖：生命周期/导航（M1）+ locator 解析/动作/断言/探索/读取（M2）。
"""

import asyncio
import base64
import difflib
import re
import struct
import time

from ...core.errors import GavelError

_TARGET_KEYS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")

# 作用域结构签名（地址/标题头 + 逐节点 tag#id.class[属性]+值/叶子文本）
def png_size(data):
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return w, h
    except Exception:
        pass
    return None, None


class PlaywrightBackend:
    def __init__(self, runtime=None):
        self.runtime = runtime

    # ---- 导航竞态：识别 + 只读操作自动重试一次 ----
    _NAV_RACE = ("Execution context was destroyed",
                 "Cannot find context",
                 "Target page, context or browser has been closed",
                 "navigating and changing the document",
                 "frame was detached",
                 "context was destroyed")

    @classmethod
    def _is_nav_race(cls, e):
        s = str(e)
        return any(k in s for k in cls._NAV_RACE)

    async def retry_on_nav_race(self, page, fn, retries=1):
        """只读操作遇导航竞态时，等页面稳定后自动重试一次；仍失败则报可重试错误。"""
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if not self._is_nav_race(e):
                raise
            if retries <= 0:
                raise GavelError("nav_race", f"页面导航竞态：{e}",
                                 retryable=True,
                                 hint="页面正在导航，稍后重试读取") from e
            try:
                await page.wait_for_load_state("domcontentloaded",
                                               timeout=5000)
            except Exception:
                await _sleep(0.3)
            return await self.retry_on_nav_race(page, fn, retries - 1)

    # ================= 导航 / 等待 =================
    async def goto(self, handle, url, *, wait_until="domcontentloaded",
                   timeout_s=30.0):
        try:
            resp = await handle.page.goto(url, wait_until=wait_until,
                                          timeout=timeout_s * 1000)
        except Exception as e:  # noqa: BLE001
            if "Download is starting" in str(e):
                raise GavelError(
                    "download_started", "导航触发了下载（不是页面）",
                    detail={"url": url}, retryable=False,
                    hint="这是下载链接：改用 page_wait_for_download 捕获下载，"
                         "或用 locator 点击后 page_wait_for_download") from e
            raise GavelError("navigation_failed", f"导航失败：{e}",
                             detail={"url": url}, retryable=True) from e
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

    async def reload(self, handle, *, wait_until="domcontentloaded",
                     timeout_s=30.0):
        await handle.page.reload(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_back(self, handle, *, wait_until="domcontentloaded",
                      timeout_s=30.0):
        await handle.page.go_back(wait_until=wait_until, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def go_forward(self, handle, *, wait_until="domcontentloaded",
                         timeout_s=30.0):
        await handle.page.go_forward(wait_until=wait_until,
                                     timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def wait_for_url(self, handle, pattern, timeout_s=30.0):
        await handle.page.wait_for_url(pattern, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": handle.page.url}

    async def wait_for_selector(self, handle, selector, *, state="visible",
                                timeout_s=30.0):
        await handle.page.wait_for_selector(selector, state=state,
                                            timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "selector": selector, "state": state}

    async def wait_for_text(self, handle, text, *, state="visible",
                            timeout_s=30.0, selector=None):
        """等某段文本出现（浏览器侧等待，满足即返回）。selector 给了就在其内部等。"""
        root = handle.page.locator(selector) if selector else handle.page
        await root.get_by_text(text, exact=False).first.wait_for(
            state=state, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "text": text, "state": state,
                "url": handle.page.url}

    async def wait_for_response(self, handle, *, url=None, status=None,
                                timeout_s=30.0):
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
            "response", predicate=match, timeout=timeout_s * 1000)
        return {"page_id": handle.page_id, "url": resp.url,
                "http_status": resp.status, "method": resp.request.method}

    # ================= dialog（不经 actor，避免与阻塞动作死锁）=================
    async def handle_dialog(self, handle, *, action="accept", prompt_text=None,
                            policy=None):
        if policy is not None:
            if policy not in ("manual", "auto_accept", "auto_dismiss"):
                raise GavelError("bad_policy", f"未知 dialog policy：{policy}")
            handle.dialog_policy = policy
        dialog = handle.pending_dialog
        if dialog is None:
            if policy is not None:
                return {"page_id": handle.page_id, "policy": policy,
                        "dialog": None}
            raise GavelError("no_dialog", "当前没有待处理 dialog",
                             detail={"history": handle.dialog_history[-3:],
                                     "hint": "dialog 可能已被 policy 自动处理"})
        info = {"page_id": handle.page_id, "dialog_type": dialog.type,
                "message": dialog.message}
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
    @staticmethod
    def _normalize_target(target):
        if not isinstance(target, dict):
            raise GavelError("bad_target", "target 必须是对象",
                             hint='如 {"by":"css","value":"#q"}')
        by = target.get("by")
        value = target.get("value")
        if by is None:
            for k in _TARGET_KEYS:
                if k in target:
                    by, value = k, target[k]
                    break
        if by is None or value is None:
            raise GavelError("bad_target", f"target 缺少 by/value：{target}",
                             hint="by 取 role|label|placeholder|text|testid|css|xpath")
        return by, value

    @staticmethod
    def _frame_root(page, frame):
        """target["frame"] 可为选择器或选择器列表（嵌套 iframe）。"""
        if not frame:
            return page
        root = page
        for f in (frame if isinstance(frame, list) else [frame]):
            root = root.frame_locator(f)
        return root

    async def resolve(self, page, target):
        by, value = self._normalize_target(target)
        name = target.get("name")
        exact = target.get("exact")
        strict = target.get("strict", True)
        nth = target.get("nth")
        has_text = target.get("has_text")
        root = self._frame_root(page, target.get("frame"))
        try:
            if by == "role":
                loc = root.get_by_role(value, name=name, exact=exact)
            elif by == "label":
                loc = root.get_by_label(value, exact=exact)
            elif by == "placeholder":
                loc = root.get_by_placeholder(value, exact=exact)
            elif by == "text":
                loc = root.get_by_text(value, exact=exact)
            elif by == "testid":
                loc = root.get_by_test_id(value)
            elif by == "css":
                loc = root.locator(value)
            elif by == "xpath":
                loc = root.locator("xpath=" + value)
            else:
                raise GavelError("bad_target", f"未知 by：{by}")
        except GavelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise GavelError("bad_target", f"非法 locator：{e}") from e
        if has_text:
            loc = loc.filter(has_text=has_text)
        if nth is not None:
            return loc.nth(nth)
        if strict:
            n = await loc.count()
            if n == 0:
                raise GavelError("locator_not_found", f"locator 无命中：{by}={value}",
                                 detail={"target": target}, retryable=True,
                                 hint="用 page_explore 找可用锚点")
            if n > 1:
                samples = await self._samples(loc)
                raise GavelError(
                    "locator_not_unique",
                    f"locator 命中 {n} 个（strict）：{by}={value}",
                    detail={"count": n, "samples": samples, "target": target},
                    hint="收窄：加 nth / has_text / 更精确的 by")
        return loc

    async def _samples(self, loc, limit=5):
        out = []
        try:
            texts = await loc.all_inner_texts()
            for t in texts[:limit]:
                out.append((t or "").strip()[:60])
        except Exception:
            pass
        return out

    # ================= 动作 =================
    async def act(self, handle, target, action, *, timeout_s=30.0, **kw):
        from .actions import handler
        return await handler(action)(self, handle, target,
                                     timeout_s=timeout_s, **kw)

    async def page_scroll(self, page, *, direction="down", amount=1,
                          timeout_s=10.0):
        dy = (1 if direction == "down" else -1) * int(amount * 800)
        await page.evaluate("(dy)=>window.scrollBy(0,dy)", dy)
        return {"action": "scroll", "direction": direction, "amount": amount}

    # ================= 读取 / 快照 =================
    async def read(self, page, page_features):
        out = {}
        errors = {}
        for name, expr in (page_features or {}).items():
            try:
                out[name] = await page.evaluate(expr)
            except Exception as e:  # noqa: BLE001
                errors[name] = str(e)
        return {"features": out, "errors": errors}

    async def text(self, page, *, selector=None, mode="text"):
        if mode == "links":
            links = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]'))"
                ".map(a => ({text:(a.innerText||'').trim(), href:a.href}))")
            return {"links": links}
        root = page.locator(selector) if selector else page.locator("body")
        if mode == "html":
            val = await root.first.inner_html()
        elif mode == "content":
            val = await root.first.text_content()
        else:
            try:
                val = await root.first.inner_text()
            except Exception:
                val = await root.first.text_content()
        return {"text": val, "mode": mode, "selector": selector}

    async def snapshot(self, page, *, level="summary", limit=50):
        info = await page.evaluate(
            "() => ({url:location.href, title:document.title, "
            "ready:document.readyState, "
            "counts:{inputs:document.querySelectorAll('input').length,"
            "buttons:document.querySelectorAll('button').length,"
            "links:document.querySelectorAll('a[href]').length,"
            "forms:document.querySelectorAll('form').length}})")
        result = {"source": "dom", "untrusted": True, **info}
        if level == "compact":
            items = await self.explore(page)
            result["elements"] = [
                {"tag": e.get("tag"), "text": (e.get("text") or "")[:60],
                 "anchor": e.get("anchor")}
                for e in items["items"][:limit]]
        elif level in ("interactive", "full"):
            items = await self.explore(page, include_all=True)
            result["elements"] = items["items"][:limit]
        if level == "full":
            html = await page.content()
            result["html"] = html
            result["html_truncated"] = False
        return result

    # ================= 探索 =================
    _EXPLORE_JS = """
    () => {
      const sel = 'a[href], button, input, select, textarea, ' +
        '[role=button],[role=link],[role=textbox],[role=checkbox],' +
        '[role=radio],[role=combobox],[role=tab],[contenteditable=true]';
      const out = [];
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        const visible = r.width > 0 && r.height > 0 &&
          s.visibility !== 'hidden' && s.display !== 'none';
        out.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          name: el.getAttribute('name'),
          type: el.getAttribute('type'),
          placeholder: el.getAttribute('placeholder'),
          aria: el.getAttribute('aria-label'),
          role: el.getAttribute('role'),
          text: (el.innerText || el.value || '').trim().slice(0, 60),
          visible: visible,
          bounds: {x: Math.round(r.x), y: Math.round(r.y),
                   w: Math.round(r.width), h: Math.round(r.height)},
        });
      }
      return out;
    }
    """

    async def explore(self, page, *, tag=None, text_contains=None, head=None,
                      tail=None, include_all=False, include_hidden=False):
        raw = await page.evaluate(self._EXPLORE_JS)
        items = []
        for el in raw:
            if not include_hidden and not el.get("visible"):
                continue
            if tag and el["tag"] != tag:
                continue
            hay = f"{el.get('text','')} {el.get('aria','')} {el.get('placeholder','')}"
            if text_contains and text_contains.lower() not in hay.lower():
                continue
            anchor, candidates = await self._anchor_for(page, el, include_all)
            el["anchor"] = anchor
            el["candidates"] = candidates
            items.append(el)
        total = len(items)
        if head:
            items = items[:head]
        if tail:
            items = items[-tail:]
        return {"items": items, "total": total}

    _FIND_TEXT_JS = """
    (arg) => {
      const t = (arg && arg.text) || '';
      const clickSel = 'a[href], button, input, select, textarea, [role=button],' +
        '[role=link],[role=tab],[contenteditable=true]';
      const seen = new Set(); const out = [];
      for (const el of document.querySelectorAll('body *')) {
        if (!t || !el.textContent || el.textContent.indexOf(t) === -1) continue;
        // 只取"最内层"包含该文本的元素，避免把外层容器也算进来
        let leaf = true;
        for (const c of el.children) {
          if (c.textContent && c.textContent.indexOf(t) !== -1) { leaf = false; break; }
        }
        if (!leaf) continue;
        let hit = el;
        while (hit && !hit.matches(clickSel)) hit = hit.parentElement;
        const target = hit || el;
        if (seen.has(target)) continue;
        seen.add(target);
        const r = target.getBoundingClientRect();
        const s = getComputedStyle(target);
        out.push({
          tag: target.tagName.toLowerCase(), id: target.id || null,
          name: target.getAttribute('name'), type: target.getAttribute('type'),
          placeholder: target.getAttribute('placeholder'),
          aria: target.getAttribute('aria-label'), role: target.getAttribute('role'),
          text: (target.innerText || target.value || '').trim().slice(0, 80),
          visible: r.width > 0 && r.height > 0 &&
                   s.visibility !== 'hidden' && s.display !== 'none' });
        if (out.length >= 40) break;
      }
      return out;
    }
    """

    async def find_text(self, page, text, *, limit=10, include_hidden=False):
        """按文本定位：返回包含该文本的"最近可点元素"及其稳定锚点。"""
        raw = await page.evaluate(self._FIND_TEXT_JS, {"text": text})
        items = []
        for el in raw:
            if not include_hidden and not el.get("visible", True):
                continue
            anchor, candidates = await self._anchor_for(page, el, False)
            el["anchor"] = anchor
            el["candidates"] = candidates
            items.append(el)
            if limit and len(items) >= limit:
                break
        return {"items": items, "total": len(items)}

    async def page_meta(self, page):
        """结构化页面元信息（标题/URL/描述/canonical/标题层级/计数/正文长度）。"""
        return await page.evaluate(
            """() => {
              const meta = (sel, attr) => { const e = document.querySelector(sel);
                return e ? (e.getAttribute(attr || 'content') || e.innerText || '').trim() : null; };
              return {
                url: location.href, title: document.title,
                lang: document.documentElement.lang || null,
                description: meta('meta[name=description]'),
                canonical: (document.querySelector('link[rel=canonical]') || {}).href || null,
                og_title: meta('meta[property="og:title"]'),
                h1: Array.from(document.querySelectorAll('h1')).slice(0,3)
                      .map(e => e.innerText.trim()).filter(Boolean),
                h2: Array.from(document.querySelectorAll('h2')).slice(0,5)
                      .map(e => e.innerText.trim()).filter(Boolean),
                links: document.querySelectorAll('a[href]').length,
                images: document.querySelectorAll('img').length,
                forms: document.querySelectorAll('form').length,
                text_len: (document.body ? document.body.innerText.length : 0) };
            }""")

    async def _anchor_for(self, page, el, include_all):
        candidates = []
        tag = el["tag"]
        # 1) id
        if el.get("id"):
            sel = f"#{el['id']}"
            n = await page.locator(sel).count()
            candidates.append({"kind": "css", "value": sel, "count": n,
                               "score": 0.96 if n == 1 else 0.5})
        # 2) name
        if el.get("name"):
            sel = f'{tag}[name="{el["name"]}"]'
            try:
                n = await page.locator(sel).count()
                candidates.append({"kind": "css", "value": sel, "count": n,
                                   "score": 0.9 if n == 1 else 0.4})
            except Exception:
                pass
        # 3) role/name
        role = el.get("role")
        if role and el.get("aria"):
            candidates.append({"kind": "role", "value": role,
                               "name": el["aria"], "count": None, "score": 0.93})
        # 4) text
        if el.get("text"):
            candidates.append({"kind": "text", "value": el["text"],
                               "count": None, "score": 0.7})
        anchor = None
        for c in candidates:
            if c.get("count") == 1:
                anchor = {k: v for k, v in c.items()
                          if k in ("kind", "value", "name")}
                break
        if anchor is None and el.get("id"):
            anchor = {"kind": "css", "value": f"#{el['id']}"}
        if anchor is None and el.get("name"):
            anchor = {"kind": "css", "value": f'{tag}[name="{el["name"]}"]'}
        if anchor is None and candidates:
            anchor = {k: v for k, v in candidates[-1].items()
                      if k in ("kind", "value", "name")}
        if not include_all and anchor is None:
            candidates.append({"kind": "css",
                               "value": self._css_path(el), "count": None,
                               "score": 0.2})
        return anchor, candidates

    @staticmethod
    def _css_path(el):
        tag = el["tag"]
        if el.get("id"):
            return f"#{el['id']}"
        if el.get("name"):
            return f'{tag}[name="{el["name"]}"]'
        return tag

    # ================= 截图 / PDF =================
    async def screenshot(self, page, *, full_page=False, selector=None,
                         timeout_s=20.0):
        ms = timeout_s * 1000

        async def _cap():
            if selector:
                return await page.locator(selector).first.screenshot(timeout=ms)
            return await page.screenshot(full_page=full_page, timeout=ms)

        try:
            data = await _cap()
        except Exception:
            # 截图是只读操作，偶发渲染时序失败重试一次
            await _sleep(0.2)
            data = await _cap()
        w, h = png_size(data)
        try:
            dpr = await page.evaluate("window.devicePixelRatio")
        except Exception:
            dpr = None
        return {"data": data, "width": w, "height": h,
                "device_scale_factor": dpr, "full_page": full_page,
                "url": page.url}

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
                    cdp.send("Page.printToPDF", {
                        "printBackground": True,
                        "preferCSSPageSize": True,
                    }),
                    timeout=timeout_s)
            finally:
                try:
                    await cdp.detach()
                except Exception:
                    pass
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

    async def evaluate_checks(self, handle, checks, before_sig=None,
                              wait_s=0.0, target=None):
        from .assertion import evaluate
        return await evaluate(self, handle, checks, before_sig=before_sig,
                              wait_s=wait_s, target=target)


async def _sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
