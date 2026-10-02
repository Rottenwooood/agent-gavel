"""Playwright 执行后端（docs/tech-plan.md §5.13）。

把 runtime 的语义操作翻译成 Playwright 调用；tools 层不直接碰 Playwright。
覆盖：生命周期/导航（M1）+ locator 解析/动作/断言/探索/读取（M2）。
"""

import difflib
import re
import struct
import time

from ..runtime.errors import GavelError

_TARGET_KEYS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")

# 作用域结构签名（地址/标题头 + 逐节点 tag#id.class[属性]+值/叶子文本）
_SIG_BODY = r"""
  function __sig(root){
    const MAX=6000;
    const bodyScope=(root===document.body);
    const out=[];
    const norm=x=>{const s=(x==null?'':String(x)).replace(/\s+/g,' ').trim();return s;};
    out.push('URL='+location.href);
    out.push('TITLE='+norm(document.title));
    let count=0;
    const w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);
    let e;
    while((e=w.nextNode())){
      if(++count>MAX)break;
      const tag=e.tagName.toLowerCase();
      let line=tag+(e.id?'#'+e.id:'');
      if(e.classList&&e.classList.length){
        const cls=Array.from(e.classList).filter(c=>!/^(sc-|jss|css-)/.test(c)).sort().join('.');
        if(cls)line+='.'+cls;
      }
      for(const a of ['name','type','role','aria-expanded','aria-hidden','placeholder','alt']){
        const av=e.getAttribute(a); if(av)line+='['+a+'='+norm(av).slice(0,60)+']';
      }
      if(e.tagName==='INPUT'||e.tagName==='TEXTAREA'){line+='='+norm(e.value).slice(0,200);}
      else if(e.tagName==='SELECT'){const so=e.selectedOptions&&e.selectedOptions[0];line+='='+norm(so?so.text:'');}
      if(!e.children.length){const tx=norm(e.textContent||e.getAttribute('placeholder')||'');if(tx)line+='::'+tx.slice(0,120);}
      out.push(line);
    }
    return {lines:out, bodyScope:bodyScope, scope:(root===document.body?'body':(root.tagName+(root.id?'#'+root.id:'')))};
  }
"""
_SIG_PAGE_JS = "() => {" + _SIG_BODY + " return __sig(document.body); }"
_SIG_SCOPE_JS = ("el => {" + _SIG_BODY +
                 " let root=null; let n=el;"
                 " while(n&&n!==document.body&&n.parentElement){"
                 "   if(/^(FORM|MAIN|SECTION|ARTICLE|DIV|UL|OL|TABLE|NAV)$/.test(n.tagName)){root=n;break;}"
                 "   n=n.parentElement; }"
                 " if(!root) root=el.parentElement||el;"
                 " return __sig(root); }")


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
    async def page_signature(self, page, target=None):
        """动作前后快照：作用域内逐节点结构签名（tag#id.class+属性+值+叶子文本），
        含地址/标题两行头。给了 target 就取"目标最近的容器"作用域（更细、
        噪音更少），否则整页 body。返回 dict，供 diff_summary 比较。"""
        if target:
            try:
                loc = (await self.resolve(page, target)).first
                return await loc.evaluate(_SIG_SCOPE_JS)
            except Exception:
                pass
        try:
            return await page.evaluate(_SIG_PAGE_JS)
        except Exception:
            return None

    @staticmethod
    def diff_summary(before, after):
        """对比前后结构签名，给出可读证据 + 是否"有意义地变化"。

        判定（沿用旧通道语义）：
          - 地址/标题头变化 → 一定是导航级变化；
          - 局部作用域（小而纯净）→ 任意 1 处增删即有意义；
          - 整页作用域（噪音多）→ 需 >=4 处增删，或变化占比 >=2%。
        """
        bl = before.get("lines") if isinstance(before, dict) else None
        al = after.get("lines") if isinstance(after, dict) else None
        if not bl or not al:
            same = before == after
            return {"changed": not same, "meaningful": not same}
        sm = difflib.SequenceMatcher(None, bl, al)
        added = removed = 0
        s_add, s_rem = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("insert", "replace"):
                added += (j2 - j1)
                s_add += al[j1:j2]
            if tag in ("delete", "replace"):
                removed += (i2 - i1)
                s_rem += bl[i1:i2]
        header_changed = bl[:2] != al[:2]
        body = bool(before.get("bodyScope")) or bool(after.get("bodyScope"))
        n_changed = added + removed
        if header_changed:
            meaningful = True
        elif body:
            total = max(len(bl), len(al))
            meaningful = n_changed >= 4 or (total > 0
                                            and n_changed / total >= 0.02)
        else:
            meaningful = n_changed >= 1
        return {"changed": n_changed > 0, "meaningful": meaningful,
                "added": added, "removed": removed,
                "scope": before.get("scope") or after.get("scope"),
                "samples_added": s_add[:6], "samples_removed": s_rem[:6]}

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
                              wait_s=0.0, interval=0.1, target=None):
        """评估 checks，返回三态（通过/不通过/拿不准）+ 明细。

        B3 事件优先：先让浏览器侧等待条件成立（成立即刻返回），不支持或
        到点则退回 Python 轮询；到点仍未通过时，用局部 diff 判"拿不准"，
        并把 diff 证据一并返回（只在拿不准时返回）。
        """
        checks = self._normalize_checks(checks)
        if not checks:
            return {"status": "pass", "assertions": []}

        deadline = time.monotonic() + max(0.0, wait_s)
        event_supported = not (target and target.get("frame"))
        if wait_s and wait_s > 0 and event_supported:
            pred = self._event_predicate(checks)
            if pred is not None:
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    await page.wait_for_function(
                        pred, timeout=int(remaining * 1000))
                except Exception:
                    pass  # 到点/表达式异常 → 落回轮询与三态判定

        last = None
        while True:
            last = await self._run_checks(page, checks)
            if all(r["passed"] for r in last) or time.monotonic() >= deadline:
                break
            await _sleep(interval)
        all_pass = all(r["passed"] for r in last)
        if all_pass:
            return {"status": "pass", "assertions": last}
        if before_sig is None:
            return {"status": "fail", "assertions": last}
        after = await self.page_signature(page, target=target)
        diff = self.diff_summary(before_sig, after)
        if diff.get("meaningful"):
            return {"status": "ambiguous", "assertions": last, "diff": diff}
        return {"status": "fail", "assertions": last}

    def _event_predicate(self, checks):
        """把可页面内表达的一组 check 合成浏览器侧等待谓词；含 iframe/不支持
        的类型返回 None（调用方退回轮询）。"""
        import json as _json

        def q(sel):
            return _json.dumps(sel or "")

        parts = []
        for c in checks:
            if c.get("frame"):
                return None
            op = c.get("op", "eq")
            sel = c.get("selector")
            read = c.get("read")
            if op in ("exists", "not_exists", "count_eq", "count_gte",
                      "count_gt"):
                if not sel:
                    return None
                read = "count"
            if read is None:
                if c.get("expr") is not None:
                    read = "expr"
                elif c.get("url"):
                    read = "url"
                elif c.get("title"):
                    read = "title"
                elif c.get("attr"):
                    read = "attr"
                elif sel:
                    read = "text"
                else:
                    return None
            if read == "url":
                a = "location.href"
            elif read == "title":
                a = "document.title"
            elif read == "expr":
                if c.get("expr") is None:
                    return None
                a = "(" + c["expr"] + ")"
            elif read == "count":
                a = f"document.querySelectorAll({q(sel)}).length"
            elif read == "value":
                a = f"(__q({q(sel)})?__q({q(sel)}).value:null)"
            elif read == "attr":
                a = (f"(__q({q(sel)})?__q({q(sel)}).getAttribute("
                     f"{_json.dumps(c.get('attr') or '')}):null)")
            elif read == "html":
                a = f"(__q({q(sel)})?__q({q(sel)}).innerHTML:null)"
            else:
                a = (f"((__q({q(sel)})&&(__q({q(sel)}).innerText"
                     f"||__q({q(sel)}).textContent))||'').trim()")
            cmp = self._cmp_js(op, a, _json.dumps(c.get("value")))
            if cmp is None:
                return None
            parts.append(cmp)
        if not parts:
            return None
        body = " && ".join(f"({p})" for p in parts)
        return ("() => { const __q=s=>s?document.querySelector(s):null; "
                f"return ({body}); }}")

    @staticmethod
    def _cmp_js(op, a, e):
        if op == "exists":
            return f"(({a})||0) > 0"
        if op == "not_exists":
            return f"(({a})||0) === 0"
        if op == "eq":
            return f"({a}) === {e}"
        if op == "neq":
            return f"({a}) !== {e}"
        if op == "contains":
            return f"String({a}).indexOf(String({e})) !== -1"
        if op == "not_contains":
            return f"String({a}).indexOf(String({e})) === -1"
        if op == "regex":
            return f"new RegExp(String({e})).test(String({a}))"
        if op in ("gt", "lt", "gte", "lte"):
            sym = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}[op]
            return f"({a}) {sym} {e}"
        if op == "count_eq":
            return f"({a}) === {e}"
        if op == "count_gte":
            return f"({a}) >= {e}"
        if op == "count_gt":
            return f"({a}) > {e}"
        return None

    @staticmethod
    def _normalize_checks(checks):
        if checks is None:
            return []
        if isinstance(checks, dict):
            # 单个 check（含 op）→ 当成一条；否则按 {名字: spec} 映射
            if "op" in checks:
                return [checks]
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
