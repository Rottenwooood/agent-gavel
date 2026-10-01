"""Playwright 执行后端（docs/tech-plan.md §5.13）。

把 runtime 的语义操作翻译成 Playwright 调用；tools 层不直接碰 Playwright。
覆盖：生命周期/导航（M1）+ locator 解析/动作/断言/探索/读取（M2）。
"""

import re
import struct
import time

from ..runtime.errors import GavelError

_TARGET_KEYS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")


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

    # ================= 导航 / 等待 =================
    async def goto(self, handle, url, *, wait_until="domcontentloaded",
                   timeout_s=30.0):
        try:
            resp = await handle.page.goto(url, wait_until=wait_until,
                                          timeout=timeout_s * 1000)
        except Exception as e:  # noqa: BLE001
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
    async def act(self, page, target, action, *, timeout_s=30.0, **kw):
        loc = await self.resolve(page, target)
        url_before = page.url
        ms = timeout_s * 1000
        if action == "click":
            await loc.click(button=kw.get("button", "left"),
                            click_count=kw.get("count", 1), timeout=ms,
                            force=kw.get("force", False))
        elif action == "fill":
            await loc.fill(kw.get("value", ""), timeout=ms)
        elif action == "type":
            text = kw.get("value", "")
            delay = kw.get("delay_ms", 0)
            try:
                await loc.press_sequentially(text, delay=delay, timeout=ms)
            except AttributeError:
                await loc.type(text, delay=delay, timeout=ms)
        elif action == "press":
            await loc.press(kw.get("keys", "Enter"), timeout=ms)
        elif action == "hover":
            await loc.hover(timeout=ms)
        elif action == "focus":
            await loc.focus(timeout=ms)
        elif action == "clear":
            await loc.fill("", timeout=ms)
        elif action == "scroll":
            await loc.scroll_into_view_if_needed(timeout=ms)
        elif action == "check":
            await loc.check(timeout=ms)
        elif action == "uncheck":
            await loc.uncheck(timeout=ms)
        elif action == "select":
            val = kw.get("value")
            by = kw.get("select_by", "value")
            if by == "label":
                await loc.select_option(label=val, timeout=ms)
            elif by == "index":
                await loc.select_option(index=int(val), timeout=ms)
            else:
                await loc.select_option(value=val, timeout=ms)
        elif action == "upload":
            await loc.set_input_files(kw.get("files"), timeout=ms)
        elif action == "drag":
            dest_loc = await self.resolve(page, kw.get("destination"))
            await loc.drag_to(dest_loc, timeout=ms)
        else:
            raise GavelError("bad_action", f"未知动作：{action}")
        result = {"action": action, "action_completed": True,
                  "url_before": url_before, "url_after": page.url}
        if kw.get("wait_navigation"):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=ms)
            except Exception:
                pass
            result["url_after"] = page.url
        return result

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
        if level in ("interactive", "full"):
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
        if selector:
            data = await page.locator(selector).first.screenshot(timeout=ms)
        else:
            data = await page.screenshot(full_page=full_page, timeout=ms)
        w, h = png_size(data)
        try:
            dpr = await page.evaluate("window.devicePixelRatio")
        except Exception:
            dpr = None
        return {"data": data, "width": w, "height": h,
                "device_scale_factor": dpr, "full_page": full_page,
                "url": page.url}

    async def pdf(self, handle, *, timeout_s=30.0):
        headless = handle.context.session.handle.opts.get("headless")
        if headless is False:
            raise GavelError("pdf_requires_headless", "page_pdf 只能在无头模式生成",
                             hint="用 AGENT_GAVEL_HEADLESS=1 启动")
        data = await handle.page.pdf()
        return {"data": data, "url": handle.page.url}

    # ================= 断言 =================
    async def page_signature(self, page):
        try:
            return await page.evaluate(
                "() => ({url:location.href, title:document.title, "
                "n:document.querySelectorAll('*').length, "
                "t:(document.body?document.body.innerText.length:0)})")
        except Exception:
            return None

    async def _actual(self, page, check):
        read = check.get("read")
        sel = check.get("selector")
        op = check.get("op")
        root = self._frame_root(page, check.get("frame"))
        if op in ("exists", "not_exists"):
            return await root.locator(sel).count() if sel else 0
        if read is None:
            if check.get("expr") is not None:
                read = "expr"
            elif check.get("url"):
                read = "url"
            elif check.get("title"):
                read = "title"
            elif check.get("attr"):
                read = "attr"
            elif check.get("count") is not None:
                read = "count"
            elif sel:
                read = "text"
            else:
                read = "title"
        if read == "url":
            return page.url
        if read == "title":
            return await page.title()
        if read == "expr":
            return await page.evaluate(check["expr"])
        if read == "count":
            return await root.locator(sel).count()
        loc = root.locator(sel).first
        if read == "value":
            return await loc.input_value()
        if read == "attr":
            return await loc.get_attribute(check["attr"])
        if read == "html":
            return await loc.inner_html()
        try:
            return (await loc.inner_text()).strip()
        except Exception:
            return (await loc.text_content()).strip()

    @staticmethod
    def _compare(op, actual, expected):
        if op == "exists":
            return (actual or 0) > 0
        if op == "not_exists":
            return (actual or 0) == 0
        a, e = actual, expected
        if op == "eq":
            return a == e
        if op == "neq":
            return a != e
        if op == "contains":
            return e is not None and str(e) in str(a)
        if op == "not_contains":
            return e is None or str(e) not in str(a)
        if op == "regex":
            return bool(re.search(str(e), str(a)))
        if op in ("gt", "lt", "gte", "lte"):
            try:
                return {"gt": a > e, "lt": a < e,
                        "gte": a >= e, "lte": a <= e}[op]
            except TypeError:
                return False
        if op == "count_eq":
            return a == e
        if op == "count_gte":
            return a >= e
        if op == "count_gt":
            return a > e
        raise GavelError("bad_op", f"未知断言 op：{op}")

    async def evaluate_checks(self, page, checks, before_sig=None,
                              wait_s=0.0, interval=0.1):
        """轮询评估 checks，返回三态 + 明细。"""
        checks = self._normalize_checks(checks)
        deadline = time.monotonic() + max(0.0, wait_s)
        last = None
        while True:
            last = await self._run_checks(page, checks)
            if all(r["passed"] for r in last) or time.monotonic() >= deadline:
                break
            await _sleep(interval)
        all_pass = all(r["passed"] for r in last)
        if all_pass:
            status = "pass"
        elif before_sig is not None:
            after = await self.page_signature(page)
            status = "ambiguous" if after != before_sig else "fail"
        else:
            status = "fail"
        return {"status": status, "assertions": last}

    @staticmethod
    def _normalize_checks(checks):
        if checks is None:
            return []
        if isinstance(checks, dict):
            out = []
            for name, spec in checks.items():
                if not isinstance(spec, dict):
                    spec = {"op": "eq", "value": spec}
                item = {"name": name}
                item.update(spec)
                out.append(item)
            return out
        return list(checks)

    async def _run_checks(self, page, checks):
        results = []
        for c in checks:
            name = c.get("name") or c.get("read") or "check"
            op = c.get("op", "eq")
            expected = c.get("value")
            try:
                actual = await self._actual(page, c)
                passed = self._compare(op, actual, expected)
                results.append({"name": name, "op": op, "expected": expected,
                                "actual": actual, "passed": passed})
            except Exception as e:  # noqa: BLE001
                results.append({"name": name, "op": op, "expected": expected,
                                "actual": None, "passed": False,
                                "error": str(e)})
        return results


async def _sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
