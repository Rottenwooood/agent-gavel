"""读取、快照、探索、截图。"""

import struct

from .util import sleep as _sleep


def png_size(data):
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return w, h
    except Exception:
        pass  # silent-ok: best-effort，失败不影响主流程
    return None, None


class ReaderMixin:
    async def page_scroll(self, page, *, direction="down", amount=1, timeout_s=10.0):
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
                ".map(a => ({text:(a.innerText||'').trim(), href:a.href}))"
            )
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
            "forms:document.querySelectorAll('form').length}})"
        )
        result = {"source": "dom", "untrusted": True, **info}
        if level == "compact":
            items = await self.explore(page)
            result["elements"] = [
                {"tag": e.get("tag"), "text": (e.get("text") or "")[:60], "anchor": e.get("anchor")}
                for e in items["items"][:limit]
            ]
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

    async def explore(
        self,
        page,
        *,
        tag=None,
        text_contains=None,
        head=None,
        tail=None,
        include_all=False,
        include_hidden=False,
    ):
        raw = await page.evaluate(self._EXPLORE_JS)
        items = []
        for el in raw:
            if not include_hidden and not el.get("visible"):
                continue
            if tag and el["tag"] != tag:
                continue
            hay = f"{el.get('text', '')} {el.get('aria', '')} {el.get('placeholder', '')}"
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
            }"""
        )

    async def _anchor_for(self, page, el, include_all):
        candidates = []
        tag = el["tag"]
        # 1) id
        if el.get("id"):
            sel = f"#{el['id']}"
            n = await page.locator(sel).count()
            candidates.append(
                {"kind": "css", "value": sel, "count": n, "score": 0.96 if n == 1 else 0.5}
            )
        # 2) name
        if el.get("name"):
            sel = f'{tag}[name="{el["name"]}"]'
            try:
                n = await page.locator(sel).count()
                candidates.append(
                    {"kind": "css", "value": sel, "count": n, "score": 0.9 if n == 1 else 0.4}
                )
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程
        # 3) role/name
        role = el.get("role")
        if role and el.get("aria"):
            candidates.append(
                {"kind": "role", "value": role, "name": el["aria"], "count": None, "score": 0.93}
            )
        # 4) text
        if el.get("text"):
            candidates.append({"kind": "text", "value": el["text"], "count": None, "score": 0.7})
        anchor = None
        for c in candidates:
            if c.get("count") == 1:
                anchor = {k: v for k, v in c.items() if k in ("kind", "value", "name")}
                break
        if anchor is None and el.get("id"):
            anchor = {"kind": "css", "value": f"#{el['id']}"}
        if anchor is None and el.get("name"):
            anchor = {"kind": "css", "value": f'{tag}[name="{el["name"]}"]'}
        if anchor is None and candidates:
            anchor = {k: v for k, v in candidates[-1].items() if k in ("kind", "value", "name")}
        if not include_all and anchor is None:
            candidates.append(
                {"kind": "css", "value": self._css_path(el), "count": None, "score": 0.2}
            )
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
    async def screenshot(self, page, *, full_page=False, selector=None, timeout_s=20.0):
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
        return {
            "data": data,
            "width": w,
            "height": h,
            "device_scale_factor": dpr,
            "full_page": full_page,
            "url": page.url,
        }
