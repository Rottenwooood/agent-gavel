"""DOM adapter：通过 Chrome CDP 操作网页。

与 adapter.py（AT-SPI，操作桌面应用）并列。两者都提供 read_state/
click/type_text/press_key 等，但来源不同：
  - adapter.py: 读桌面应用的无障碍树
  - dom_adapter.py: 读浏览器页面的 DOM

DOM 状态被表达成与 AT-SPI 节点同构的列表（role=标签, name=文本,
value=输入值），这样 agent-claw 的 normalize/diff/断言目录全部复用，
无需为网页另写一套验证逻辑。

运行前提：浏览器以 --remote-debugging-port 启动，本模块通过 CDP
WebSocket 连接。
"""

import asyncio
import json
import urllib.request

import websockets


def _el_js(selector):
    """把探索锚点转成"返回元素或 null"的 JS 片段。

    支持三种锚点：
      - 普通 CSS 选择器（'#kw'、'input[name=q]'）
      - 文本锚点（'__text__:完整文本'）——文本在页内唯一
      - 组内文本锚点（'__text_nth__:N::文本'）——文本在页内重复，
        取第 N 个匹配（N 从 0 起）。解决"多个同名按钮"定位。
    """
    if isinstance(selector, str) and selector.startswith("__text_nth__:"):
        rest = selector[len("__text_nth__:"):]
        # 格式 N::text，取第一个 :: 前为 N，后为文本
        idx_str, _, target = rest.partition("::")
        try:
            idx = int(idx_str)
        except ValueError:
            idx = 0
        return (f"Array.from(document.querySelectorAll('button, a[href], form, "
                f"input, textarea, select, div, span')).filter(el => "
                f"(el.textContent || el.value || el.placeholder || '').trim() === "
                f"{json.dumps(target)})[{idx}]")
    if isinstance(selector, str) and selector.startswith("__text__:"):
        target = selector[len("__text__:"):]
        return (f"Array.from(document.querySelectorAll('button, a[href], form, "
                f"input, textarea, select, div, span')).find(el => "
                f"(el.textContent || el.value || el.placeholder || '').trim() === "
                f"{json.dumps(target)})")
    return f"document.querySelector({json.dumps(selector)})"


class DomClient:
    def __init__(self, debug_url="http://127.0.0.1:9222", page_url_match=None):
        self._debug_url = debug_url
        self._page_url_match = page_url_match or ("baidu" if "baidu" in str(page_url_match) else None)
        self._ws = None
        self._mid = 0

    async def __aenter__(self):
        ws_url = await self._find_page_ws()
        if not ws_url:
            raise RuntimeError("no matching CDP page target")
        self._ws = await websockets.connect(ws_url, max_size=20 * 1024 * 1024)
        return self

    async def __aexit__(self, *exc):
        if self._ws:
            await self._ws.close()

    async def _find_page_ws(self):
        with urllib.request.urlopen(f"{self._debug_url}/json/list", timeout=3) as r:
            targets = json.loads(r.read())
        pages = [t for t in targets if t["type"] == "page"]
        # 优先匹配指定 URL；否则取第一个页面
        for t in pages:
            if self._page_url_match and self._page_url_match in t["url"]:
                return t["webSocketDebuggerUrl"]
        for t in pages:
            if not t.get("url", "").startswith("chrome"):
                return t["webSocketDebuggerUrl"]
        return pages[0]["webSocketDebuggerUrl"] if pages else None

    async def cmd(self, method, params=None):
        self._mid += 1
        mid = self._mid
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    async def eval_js(self, expression, return_by_value=True):
        """执行 JS，返回结果值。"""
        r = await self.cmd("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": return_by_value,
            "awaitPromise": True,
        })
        res = r.get("result", {})
        if res.get("type") == "object" and res.get("subtype") == "error":
            raise RuntimeError(f"JS error: {res.get('description')}")
        return res.get("value")

    # ---- 页面状态读取（转成与 AT-SPI 同构的节点列表）----

    async def read_state(self, selector=None):
        """读当前页面：把可交互元素提取成归一化友好节点。

        返回 [{role, name, value, ...}]，role=标签小写，
        name=可读文本，value=input 的当前值。
        """
        js = """
        (() => {
            const els = document.querySelectorAll('input, textarea, button, select, a[href], form');
            const out = [];
            const seen = new Set();
            for (const el of els) {
                if (seen.has(el)) continue;
                seen.add(el);
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue; // 隐藏
                const tag = el.tagName.toLowerCase();
                const name = (el.name || '') + '#' + (el.id || '');
                const text = (el.value !== undefined ? el.value
                              : el.textContent || el.placeholder || '').toString().trim().slice(0, 80);
                const placeholder = el.placeholder || '';
                out.push({
                    role: tag,
                    tag: tag,
                    name: (el.name || '') || name,
                    id: el.id || '',
                    text: text || placeholder,
                    value: el.value !== undefined ? el.value : null,
                    placeholder: placeholder,
                    aria: el.getAttribute('aria-label') || '',
                    idx: out.length,
                });
            }
            return out;
        })()
        """
        nodes = await self.eval_js(js)
        return nodes or []

    # ---- 操作 ----

    async def focus_and_clear(self, selector):
        """聚焦元素并清空（如搜索框）。"""
        return await self.clear(selector)

    async def focus(self, selector):
        """仅聚焦元素，不清空其值（操作前准备，保留已填内容）。"""
        el_js = _el_js(selector)
        return await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return {{ok: false, error: 'not found: ' + {json.dumps(selector)}}};
            el.focus();
            return {{ok: true, tag: el.tagName}};
        }})()
        """)

    async def _clear_trusted(self, selector):
        """trusted 清空：focus → 全选 → Backspace 删除。

        对 React 受控组件，el.value='' 不更新内部 state（合成赋值无效），
        必须用真实键盘输入 Ctrl+A + Backspace 让框架收到真实删除事件。
        """
        el_js = _el_js(selector)
        r = await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return {{ok: false, error: 'not found: ' + {json.dumps(selector)}}};
            el.focus();
            el.select();
            return {{ok: true, has: (el.value || '')}};
        }})()
        """)
        if not r or not r.get("ok"):
            return r
        # Ctrl+A 全选（受控组件需真实 keydown 触发 select-all 语义）
        for key, code, vk in (("Control", "ControlLeft", 17),):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "modifiers": 0})
        for key, code, vk in (("a", "KeyA", 65),):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "modifiers": 2})  # Ctrl held
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "modifiers": 2})
        for key, code, vk in (("Control", "ControlLeft", 17),):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "modifiers": 2})
        # Backspace 删除选中内容
        for t in ("keyDown", "keyUp"):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": t, "key": "Backspace", "code": "Backspace",
                "windowsVirtualKeyCode": 8})
        return await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return {{ok: false, error: 'not found'}};
            return {{ok: true, value: el.value}};
        }})()
        """)

    async def clear(self, selector, trusted=True):
        """清空输入框。

        trusted=True（默认）：真实键盘 Ctrl+A+Backspace（isTrusted=true），
        框架能收到真实删除，知乎等受控组件必须用。
        trusted=False：el.value=''（合成，快）。对受控组件无效。
        """
        el_js = _el_js(selector)
        if not trusted:
            return await self.eval_js(f"""
            (() => {{
                const el = {el_js};
                if (!el) return {{ok: false, error: 'not found: ' + {json.dumps(selector)}}};
                el.focus();
                if (el.value !== undefined) {{ el.value = ''; }}
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                return {{ok: true, value: el.value}};
            }})()
            """)
        return await self._clear_trusted(selector)

    async def set_value(self, selector, value, trusted=True):
        """设值。

        trusted=True（默认）：真实清空 + CDP Input.insertText 真实键入
        （isTrusted=true，走 IME/键盘输入通道，框架无法区分）。慢但可靠，
        知乎等 React 重渲染站必须用。
        trusted=False：直接设 DOM value + 触发 input/change 事件（合成，
        isTrusted=false）。快，但较真的框架可能冲掉。
        """
        el_js = _el_js(selector)
        if not trusted:
            return await self.eval_js(f"""
            (() => {{
                const el = {el_js};
                if (!el) return {{ok: false, error: 'not found: ' + {json.dumps(selector)}}};
                el.focus();
                el.value = {json.dumps(value)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{ok: true, value: el.value}};
            }})()
            """)
        # trusted 模式：真实清空 → 真实键入（清空不能用合成 el.value=''，
        # 受控组件不更新 state，会残留旧值导致键入值拼接/冲突）
        clear_res = await self._clear_trusted(selector)
        if not clear_res or not clear_res.get("ok"):
            return clear_res
        try:
            await self.cmd("Input.insertText", {"text": value})
        except Exception as e:
            return {"ok": False, "error": f"Input.insertText failed: {e}"}
        return await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return {{ok: false, error: 'not found'}};
            return {{ok: true, value: el.value}};
        }})()
        """)

    async def click(self, selector, trusted=True):
        """点击元素（支持 __text__: 文本锚点）。

        trusted=True（默认）：CDP Input.dispatchMouseEvent 真实点击（isTrusted=true），
        对合成 click 不响应的重框架站点可靠。
        trusted=False：合成 el.click()（isTrusted=false），快。
        """
        el_js = _el_js(selector)
        if not trusted:
            return await self.eval_js(f"""
            (() => {{
                const el = {el_js};
                if (!el) return {{ok: false, error: 'not found: ' + {json.dumps(selector)}}};
                el.scrollIntoView({{block: 'center'}});
                el.click();
                return {{ok: true}};
            }})()
            """)
        # trusted 模式：真实鼠标点击。先求元素中心点坐标
        pt = await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return null;
            el.scrollIntoView({{block: 'center'}});
            const r = el.getBoundingClientRect();
            return {{x: r.left + r.width / 2, y: r.top + r.height / 2,
                     vw: window.innerWidth, vh: window.innerHeight}};
        }})()
        """)
        if not pt:
            return {"ok": False, "error": f"not found: {selector}"}
        if not (0 <= pt["x"] <= pt["vw"] and 0 <= pt["y"] <= pt["vh"]):
            return {"ok": False, "error": "element off-screen",
                    "point": pt, "hint": "scroll it into view first"}
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": pt["x"], "y": pt["y"],
                        "button": "left", "clickCount": 1})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": pt["x"], "y": pt["y"],
                        "button": "left", "clickCount": 1})
        return {"ok": True, "point": pt}

    async def press_enter(self, trusted=True):
        """在当前焦点元素上触发回车。

        trusted=True（默认）：CDP Input.dispatchKeyEvent 发真实回车键
        （isTrusted=true），对合成键盘不响应的框架可靠。
        trusted=False：合成 KeyboardEvent（isTrusted=false）+ 尝试 form submit，快。
        """
        if not trusted:
            return await self.eval_js("""
            (() => {
                const el = document.activeElement;
                if (!el) return {ok: false, error: 'no focus'};
                const ev = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true});
                el.dispatchEvent(ev);
                // 某些站点监听 keyup / 表单 submit
                const ev2 = new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true});
                el.dispatchEvent(ev2);
                // 若在 form 里，尝试 submit
                const form = el.closest('form');
                if (form && form.requestSubmit) { form.requestSubmit(); }
                return {ok: true, tag: el.tagName};
            })()
            """)
        # trusted 模式：真实按键事件。keyDown + char + keyUp（Enter）
        for t, k, code in (("keyDown", "Enter", "Enter"), ("keyUp", "Enter", "Enter")):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": t, "key": k, "code": code,
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
        # 兜底：真实回车对"监听 keydown 的受控站"有效；但对"仅认 form 提交"的站
        # (如豆瓣搜索框，无 keydown 监听)不触发。JS requestSubmit 覆盖后者。
        # 两者叠加不冲突：已发真实回车，这里只补 form 语义提交。
        return await self.eval_js("""
        (() => {
            const el = document.activeElement;
            const form = el && el.closest ? el.closest('form') : null;
            if (form && form.requestSubmit) { form.requestSubmit(); }
            return {ok: true, submitted: !!(form && form.requestSubmit)};
        })()
        """)

    async def navigate(self, url):
        """导航到新 URL。用 CDP Page.navigate（比 JS location.href 可靠，
        跨站跳转不丢上下文）。"""
        try:
            await self.cmd("Page.enable")
        except Exception:
            pass
        await self.cmd("Page.navigate", {"url": url})
        await self.wait_page_load()
        return "navigating"

    async def get_title(self):
        return await self.eval_js("document.title")

    async def explore(self, interactive_only=True):
        """探索当前页面：枚举可交互元素，为每个生成"经校验的稳定锚点"。

        锚点优先级（每级都做唯一性校验才采用）：
          1. id（CSS 选择器）
          2. name（CSS 属性选择器）
          3. 完整文本/placeholder（对 button/a/link——CSS 无法按文本选，
             所以生成 JS 定位表达式，形式 "__text__:完整文本"，由操作端解析）
        锚点不存在则 unique=false，why='no-unique-selector'。

        返回 [{selector, tag, type, id, name, text, placeholder,
               visible, unique, why}]，覆盖 input/textarea/select/button/a/form。
        """
        js = r"""
        (() => {
            const escId = (id) => '#' + CSS.escape(id);
            const escAttr = (v) => "'" + String(v).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
            const q1 = (sel) => {
                try { return document.querySelectorAll(sel).length === 1; }
                catch (e) { return false; }
            };
            // 完整文本出现次数 + 记录每个文本已见过的实例数（用于组内编号）
            const textCount = {};
            const textSeen = {};
            // 强交互特征(role/tabindex/onclick)——非语义元素(div/span 实现的
            // tab、菜单、自定义按钮，React 站点很常见)只有当此才列为可交互；
            // 仅 cursor:pointer 是弱特征(链接式 div 遍地)，不收避免噪音
            const strongClick = (e) => {
                if (e.children.length > 0) return false;
                const txt = (e.textContent || e.value || '').trim();
                if (txt.length > 30 || !txt) return false;
                return e.getAttribute('role')
                    || e.hasAttribute('tabindex')
                    || e.onclick != null;
            };
            const els = document.querySelectorAll(
                'input, textarea, select, button, a[href], form, '
                + 'div[role], span[role], [tabindex], [onclick], '
                + 'div, span');
            const list = Array.prototype.slice.call(els).filter(el => {
                const tag = el.tagName.toLowerCase();
                if ((tag === 'input' && el.type === 'hidden')) return false;
                if (tag === 'div' || tag === 'span') {
                    // 非语义元素只保留"叶子 + 强可点击特征(role/tabindex/onclick)"，
                    // 避免页脚/文案等纯 pointer 元素的噪音
                    return strongClick(el);
                }
                if (tag === 'form') {
                    // 表单容器：自身文本超长说明只是包装(内容在子元素)，不作为可点击目标
                    return (el.textContent || '').trim().length <= 30;
                }
                return true;
            });
            for (const el of list) {
                if (el.tagName.toLowerCase() === 'input' && el.type === 'hidden') continue;
                const t = (el.textContent || el.value || el.placeholder || '').trim();
                if (t) textCount[t] = (textCount[t] || 0) + 1;
            }
            const out = [];
            let seq = 0;
            for (const el of list) {
                if (el.tagName.toLowerCase() === 'input' && el.type === 'hidden') continue;
                const r = el.getBoundingClientRect();
                const visible = r.width > 5 && r.height > 5;
                const tag = el.tagName.toLowerCase();
                const id = el.id || '';
                const name = el.name || '';
                const placeholder = el.placeholder || '';
                const type = el.type || (tag === 'a' ? 'link' : '');
                const fullText = (el.textContent || '').trim();
                const text = fullText
                    ? fullText.slice(0, 60)
                    : (placeholder || '').slice(0, 60);

                // 逐级尝试稳定锚点
                let selector = '';
                let why = '';
                let unique = false;
                let same_text_count = 0;
                if (id) {
                    const s = escId(id);
                    if (q1(s)) { selector = s; why = 'id'; unique = true; }
                }
                if (!selector && name && tag !== 'form') {
                    const s = `${tag}[name=${escAttr(name)}]`;
                    if (q1(s)) { selector = s; why = 'name'; unique = true; }
                }
                if (!selector && fullText) {
                    const cnt = textCount[fullText] || 0;
                    same_text_count = cnt;
                    if (cnt === 1) {
                        // 文本在页内唯一
                        selector = '__text__:' + fullText;
                        why = 'text';
                        unique = true;
                    } else {
                        // 文本重复：给组内序号，可精确定位第 N 个
                        const seen = textSeen[fullText] = (textSeen[fullText] || 0) + 1;
                        selector = `__text_nth__:${seen - 1}::${fullText}`;
                        why = 'text-nth';
                        unique = true;
                    }
                }

                out.push({
                    index: seq++,           // 页内可交互元素序号（稳定遍历序）
                    selector: selector || null,
                    tag, type, id, name,
                    placeholder,
                    text,
                    visible, unique,
                    why: selector ? why : 'no-unique-selector',
                    same_text_count,
                    value: el.value !== undefined ? el.value : null,
                });
            }
            return out;
        })()
        """
        return await self.eval_js(js) or []

    async def wait_page_load(self, settle_s=1.5, timeout_s=15.0):
        """等页面加载完成。

        导航后 JS context 会重建，立即 eval 会挂起/报错，所以先固定等
        settle_s 让 context 就绪，再轮询 readyState；eval 失败则重试。
        """
        import time
        await asyncio.sleep(settle_s)
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            try:
                state = await self.eval_js("document.readyState")
                if state == "complete":
                    # 再等一拍让动态内容渲染
                    await asyncio.sleep(0.8)
                    return True
            except Exception:
                pass  # context 未就绪，重试
            await asyncio.sleep(0.4)
        return False
