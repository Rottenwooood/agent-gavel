"""DOM adapter：通过 Chrome CDP 操作网页。

与 adapter.py（AT-SPI，操作桌面应用）并列。两者都提供 read_state/
click/type_text/press_key 等，但来源不同：
  - adapter.py: 读桌面应用的无障碍树
  - dom_adapter.py: 读浏览器页面的 DOM

DOM 状态被表达成与 AT-SPI 节点同构的列表（role=标签, name=文本,
value=输入值），这样 agent-gavel 的 normalize/diff/断言目录全部复用，
无需为网页另写一套验证逻辑。

运行前提：浏览器以 --remote-debugging-port 启动，本模块通过 CDP
WebSocket 连接。
"""

import asyncio
import json
import time
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


# CDP Input.dispatchKeyEvent 键盘码表：人类键名 -> (key, code, windowsVirtualKeyCode)。
# 覆盖字母/数字/常用功能键/方向/编辑键。修饰键单独处理(不进码表)。
_KEYS = {
    # 字母
    **{c: (c, f"Key{c.upper()}", ord(c.upper())) for c in "abcdefghijklmnopqrstuvwxyz"},
    # 数字
    **{str(d): (str(d), f"Digit{d}", 0x30 + int(d)) for d in range(10)},
    # 功能键
    **{f"F{i}": (f"F{i}", f"F{i}", 0x70 + (i - 1)) for i in range(1, 13)},
    # 编辑/导航键
    "Enter": ("Enter", "Enter", 13),
    "Return": ("Enter", "Enter", 13),
    "Tab": ("Tab", "Tab", 9),
    "Backspace": ("Backspace", "Backspace", 8),
    "Delete": ("Delete", "Delete", 46),
    "Del": ("Delete", "Delete", 46),
    "Escape": ("Escape", "Escape", 27),
    "Esc": ("Escape", "Escape", 27),
    "Space": (" ", "Space", 32),
    "Home": ("Home", "Home", 36),
    "End": ("End", "End", 35),
    "PageUp": ("PageUp", "PageUp", 33),
    "PageDown": ("PageDown", "PageDown", 34),
    "ArrowUp": ("ArrowUp", "ArrowUp", 38),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39),
    "Up": ("ArrowUp", "ArrowUp", 38),
    "Down": ("ArrowDown", "ArrowDown", 40),
    "Left": ("ArrowLeft", "ArrowLeft", 37),
    "Right": ("ArrowRight", "ArrowRight", 39),
    "Insert": ("Insert", "Insert", 45),
    # 标点/符号(常见)
    "-": ("-", "Minus", 0xBD), "=": ("=", "Equal", 0xBB),
    "[": ("[", "BracketLeft", 0xDB), "]": ("]", "BracketRight", 0xDD),
    "\\": ("\\", "Backslash", 0xDC), ";": (";", "Semicolon", 0xBA),
    "'": ("'", "Quote", 0xDE), ",": (",", "Comma", 0xBC),
    ".": (".", "Period", 0xBE), "/": ("/", "Slash", 0xBF),
    "`": ("`", "Backquote", 0xC0),
}

# 修饰键名 -> (key, code, windowsVirtualKeyCode, CDP modifiers 位)
_MODIFIERS = {
    "Control": ("Control", "ControlLeft", 17, 2),
    "Ctrl": ("Control", "ControlLeft", 17, 2),
    "Alt": ("Alt", "AltLeft", 18, 1),
    "Shift": ("Shift", "ShiftLeft", 16, 8),
    "Meta": ("Meta", "MetaLeft", 91, 4),
    "Super": ("Meta", "MetaLeft", 91, 4),
    "Command": ("Meta", "MetaLeft", 91, 4),
}


def _key_spec(name):
    """把人类键名解析成 CDP 按键事件参数。

    name 可带修饰键前缀，如 "Ctrl+A"、"Shift+Tab"、"Ctrl+Alt+Delete"。
    返回 {"modifiers": [mod_spec...], "key": (key, code, vk), "mod_bits": int}
    """
    mods = {"Control": 0, "Alt": 0, "Shift": 0, "Meta": 0}
    mod_specs = []
    parts = name.split("+")
    rest = []
    for p in parts:
        key = p.strip()
        if key in _MODIFIERS and len(parts) > 1 and p is not parts[-1]:
            # 是修饰键前缀(非最后一个)
            mk, mc, mv, mbit = _MODIFIERS[key]
            mods[mk] = 1
            mod_specs.append((mk, mc, mv, mbit))
        elif key in _MODIFIERS and len(parts) == 1:
            # 单独按修饰键本身
            rest.append(key)
        else:
            rest.append(key)
    if not rest:
        # 只有修饰键(如单独按 Ctrl)——需要至少一个按键
        if mod_specs:
            mk, mc, mv, mbit = mod_specs[0]
            return {"modifiers": mod_specs, "key": (mk, mc, mv),
                    "mod_bits": mods, "is_modifier_only": True}
        return None
    key = rest[-1]
    if key not in _KEYS:
        # 单字符(非表内)当作直接文本键：key=字符
        if len(key) == 1:
            return {"modifiers": mod_specs, "key": (key, "", 0),
                    "mod_bits": mods, "raw_char": True}
        return None
    k, code, vk = _KEYS[key]
    return {"modifiers": mod_specs, "key": (k, code, vk),
            "mod_bits": mods}


class DomClient:
    def __init__(self, debug_url="http://127.0.0.1:9222", page_url_match=None):
        self._debug_url = debug_url
        self._page_url_match = page_url_match or ("baidu" if "baidu" in str(page_url_match) else None)
        self._ws = None
        self._mid = 0

    async def __aenter__(self):
        # T1: DOM 通道自管 Chrome——连接前确保调试口在(未起则自启, 死了则重拉)
        from agent_gavel.browser_manager import ensure_chrome
        from urllib.parse import urlparse
        port = 9222
        try:
            port = urlparse(self._debug_url).port or 9222
        except Exception:
            pass
        boot = ensure_chrome(port=port)
        if not boot.get("ok"):
            raise RuntimeError(f"chrome not available: {boot.get('error')}")
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

    async def cmd(self, method, params=None, timeout=10.0):
        self._mid += 1
        mid = self._mid
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP {method} timed out after {timeout}s")
            try:
                msg = json.loads(await asyncio.wait_for(self._ws.recv(), remaining))
            except asyncio.TimeoutError:
                raise TimeoutError(f"CDP {method} timed out after {timeout}s") from None
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    async def eval_js(self, expression, return_by_value=True, timeout=10.0):
        """执行 JS，返回结果值。"""
        r = await self.cmd("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": return_by_value,
            "awaitPromise": True,
        }, timeout=timeout)
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

    async def click(self, selector, trusted=True, button="left", count=1):
        """点击元素（支持 __text__: 文本锚点）。

        button: left|right|middle（右键=上下文菜单，中键=新开等）。
        count: 点击次数（1=单击, 2=双击）。
        trusted=True（默认）：CDP Input.dispatchMouseEvent 真实点击（isTrusted=true），
        对合成 click 不响应的重框架站点可靠。
        trusted=False：合成 el.click()（isTrusted=false），快（仅左键单击）。
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
        # trusted 模式：真实鼠标事件。先求元素中心点坐标
        pt = await self._center_of(selector)
        if not pt:
            return {"ok": False, "error": f"not found or invisible: {selector}"}
        if not (0 <= pt["x"] <= pt["vw"] and 0 <= pt["y"] <= pt["vh"]):
            return {"ok": False, "error": "element off-screen",
                    "point": pt, "hint": "scroll it into view first"}
        btn = "right" if button == "right" else ("middle" if button == "middle" else "left")
        # 真实按键序列（支持双击：clickCount 由 CDP 判断）
        for i in range(max(1, int(count))):
            cc = 1 if count == 1 else (i + 1)
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": "mousePressed", "x": pt["x"], "y": pt["y"],
                            "button": btn, "clickCount": cc})
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": "mouseReleased", "x": pt["x"], "y": pt["y"],
                            "button": btn, "clickCount": cc})
        return {"ok": True, "point": pt, "button": btn, "count": count}

    async def scroll(self, direction="down", amount=1.0, selector=None):
        """滚动页面（或元素内）。direction: down|up。amount: 约多少屏。

        selector 给则滚那个可滚动元素内部；不给滚 window。
        """
        js_target = ("document.scrollingElement || document.documentElement"
                     if not selector else _el_js(selector))
        delta = int(amount * 800) * (1 if direction == "down" else -1)
        return await self.eval_js(f"""
        (() => {{
            const el = {js_target};
            if (!el) return {{ok: false, error: 'not found'}};
            el.scrollBy({{top: {delta}, behavior: 'instant'}});
            return {{ok: true, top: el.scrollTop}};
        }})()
        """)

    async def _center_of(self, selector):
        """返回元素中心点 + 视口尺寸（供真实鼠标事件用）。返回 None 若找不到/不可见。"""
        el_js = _el_js(selector)
        pt = await self.eval_js(f"""
        (() => {{
            const el = {el_js};
            if (!el) return null;
            el.scrollIntoView({{block: 'center'}});
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return null;
            return {{x: r.left + r.width / 2, y: r.top + r.height / 2,
                     vw: window.innerWidth, vh: window.innerHeight}};
        }})()
        """)
        return pt

    # ============ 原子输入原语 ============

    async def press_key(self, keys, trusted=True):
        """发任意按键/组合键（参数化，非每键一函数）。

        keys: "Enter" / "Tab" / "Ctrl+A" / "Shift+Tab" / "ArrowDown" / "F5" 等。
        修饰键前缀可多个：Ctrl+Alt+Delete。单个修饰键："Control"。
        trusted=True(默认): CDP Input.dispatchKeyEvent 真实按键(isTrusted=true)。
        trusted=False: 合成 KeyboardEvent。
        """
        spec = _key_spec(keys)
        if not spec:
            return {"ok": False, "error": f"unsupported key: {keys!r}",
                    "hint": "见 _KEYS 支持表；修饰键用 + 前缀如 Ctrl+A"}
        if not trusted:
            return await self._press_key_synthetic(keys, spec)
        return await self._press_key_trusted(spec)

    async def _press_key_trusted(self, spec):
        key, code, vk = spec["key"]
        mb = spec["mod_bits"]
        modifiers_bits = 0
        for mk in ("Control", "Alt", "Shift", "Meta"):
            if mb.get(mk):
                modifiers_bits |= {"Control": 2, "Alt": 1, "Shift": 8, "Meta": 4}[mk]
        # 先按下修饰键(若有)
        for mk, mc, mv, mbit in spec.get("modifiers", []):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": mk, "code": mc,
                "windowsVirtualKeyCode": mv, "modifiers": modifiers_bits})
        # 主键：keyDown (无修饰) / rawKeyDown (有修饰, 触发浏览器编辑加速键
        # 如 Ctrl+A 全选) + keyUp。char 仅无修饰可打印键发。
        k, c, v = key, code, vk
        has_mods = modifiers_bits != 0
        down_type = "rawKeyDown" if has_mods else "keyDown"
        # Windows: CDP 裸 Enter(缺 nativeVirtualKeyCode) 不触发表单提交等
        # 系统级行为(Chrome 在 Windows 按 native key code 路由)。
        native = 13 if k == "Enter" else None
        down = {"type": down_type, "key": k, "code": c or "",
                "windowsVirtualKeyCode": v or 0, "modifiers": modifiers_bits}
        up = {"type": "keyUp", "key": k, "code": c or "",
              "windowsVirtualKeyCode": v or 0, "modifiers": modifiers_bits}
        if native is not None:
            down["nativeVirtualKeyCode"] = native
            up["nativeVirtualKeyCode"] = native
        await self.cmd("Input.dispatchKeyEvent", down)
        if (spec.get("raw_char") or (k and len(k) == 1
                                     and not spec.get("is_modifier_only"))) and not has_mods:
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "char", "text": k, "unmodifiedText": k,
                "windowsVirtualKeyCode": v or 0, "modifiers": modifiers_bits})
        await self.cmd("Input.dispatchKeyEvent", up)
        # Enter 兜底：与 press_enter 一致。仅原生按键对"监听 keydown 的受控站"
        # 有效；对只认 form 提交的站(必应首页等)原生 Enter 仍可能不触发(实测
        # Windows 上 CDP Enter 无 requestSubmit 不提交)。补 form 语义提交。
        if k == "Enter" and not has_mods:
            await self.eval_js("""
            (() => {
                const el = document.activeElement;
                const form = el && el.closest ? el.closest('form') : null;
                if (form && form.requestSubmit) { form.requestSubmit(); }
                return {ok: true, submitted: !!(form && form.requestSubmit)};
            })()
            """)
        # 松开修饰键
        for mk, mc, mv, mbit in spec.get("modifiers", []):
            await self.cmd("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": mk, "code": mc,
                "windowsVirtualKeyCode": mv, "modifiers": modifiers_bits})
        return {"ok": True, "keys": key}

    async def _press_key_synthetic(self, keys, spec):
        """合成 KeyboardEvent 版本（快，isTrusted=false）。"""
        key = spec["key"][0]
        return await self.eval_js(f"""
        (() => {{
            const el = document.activeElement;
            if (!el) return {{ok: false, error: 'no focus'}};
            for (const t of ['keydown','keyup']) {{
                el.dispatchEvent(new KeyboardEvent(t, {{
                    key: {json.dumps(key)}, bubbles: true, cancelable: true}}));
            }}
            return {{ok: true}};
        }})()
        """)

    async def type_text(self, text):
        """真实输入任意文本（含中文/emoji）。走 CDP Input.insertText（IME 通道，
        isTrusted=true）。文本整体注入，不做逐键。
        """
        try:
            await self.cmd("Input.insertText", {"text": text})
        except Exception as e:
            return {"ok": False, "error": f"Input.insertText failed: {e}"}
        return {"ok": True}

    async def hover(self, selector, trusted=True):
        """鼠标悬停在元素上（触发 tooltip/hover 态）。"""
        if not trusted:
            return {"ok": False, "error": "hover 需要 trusted(真实鼠标移动)"}
        pt = await self._center_of(selector)
        if not pt:
            return {"ok": False, "error": f"not found or invisible: {selector}"}
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseMoved", "x": pt["x"], "y": pt["y"]})
        return {"ok": True, "point": pt}

    async def drag(self, src_selector, dst_selector=None, *, dx=None, dy=None,
                   trusted=True):
        """拖拽：从元素到目标元素（HTML5 drag/drop），或按像素偏移。

        dst_selector: 拖到哪个元素上。
        dx/dy: 或给像素偏移。
        """
        if not trusted:
            return {"ok": False, "error": "drag 需要 trusted(真实鼠标)"}
        if not dst_selector and dx is None and dy is None:
            return {"ok": False, "error": "drag 需要 dst_selector 或 dx/dy"}
        s = await self._center_of(src_selector)
        if not s:
            return {"ok": False, "error": f"source not found: {src_selector}"}
        if dst_selector:
            d = await self._center_of(dst_selector)
            if not d:
                return {"ok": False, "error": f"dest not found: {dst_selector}"}
            ex, ey = d["x"], d["y"]
        else:
            ex, ey = s["x"] + dx, s["y"] + dy
        # 真实拖拽序列
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": s["x"], "y": s["y"],
                        "button": "left", "clickCount": 1})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseMoved", "x": s["x"] + (ex - s["x"]) * 0.5,
                        "y": s["y"] + (ey - s["y"]) * 0.5, "button": "left"})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseMoved", "x": ex, "y": ey, "button": "left"})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": ex, "y": ey,
                        "button": "left", "clickCount": 1})
        return {"ok": True, "from": {"x": s["x"], "y": s["y"]},
                "to": {"x": ex, "y": ey}}

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

    async def wait_page_load(self, settle_s=0.0, timeout_s=15.0):
        """等页面加载完成（事件不依赖固定 sleep）。

        不固定等——直接轮询 readyState；eval 超时/抛错（导航中 context 重建）
        由 cmd 超时保护转异常，这里重试即可。readyState complete 立即返回，
        渲染兜底交给动作断言轮询。
        """
        if settle_s:
            await asyncio.sleep(settle_s)
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            try:
                # 导航中 context 重建会让 eval 挂起/报错——短超时(3s)让它快速
                # 失败进重试，而非卡满 cmd 默认 10s
                state = await self.eval_js("document.readyState", timeout=3.0)
                if state == "complete":
                    return True
            except Exception:
                pass  # context 未就绪或 eval 超时，重试
            await asyncio.sleep(0.1)
        return False

    async def wait_event(self, page_features, expected_feature, timeout_s=6.0):
        """事件驱动等待：把断言下沉到页面，用 MutationObserver 唤醒，一次调用等到底。

        适用于"动作后页面会异步变化、且变化会反映到 DOM"的场景（提交后结果区
        出现、loading 消失等）。页面内：
          - 立即判一次；不满足则 MutationObserver 监听 body 子树，
            DOM 变化时重判；满足即 resolve，返回当前各 feature 实际值。
          - timeout_s 超时 resolve，返回最后一次实际值。
        返回 {"status": "pass"|"timeout", "values": {feature: actual}}。
        判定逻辑在此方法内下沉为页面 JS（eq/neq/exists/not_exists/contains）。
        """
        import json as _json
        feats = _json.dumps(page_features or {}, ensure_ascii=False)
        exps = _json.dumps(expected_feature or {}, ensure_ascii=False)
        timeout_ms = int(timeout_s * 1000)
        js = r"""
        (async () => {
          const page_features = %s;
          const expected = %s;
          const timeoutMs = %d;
          const evalFeat = (name, expr) => { try { return eval(expr); } catch (e) { return undefined; } };
          const checkPass = (values) => {
            for (const name in expected) {
              const expect = expected[name];
              const actual = values[name];
              const op = expect.op || 'eq';
              let passed;
              if (op === 'eq') passed = (actual === expect.value);
              else if (op === 'neq') passed = (actual !== expect.value);
              else if (op === 'exists') passed = !(actual === undefined || actual === null || actual === '' || actual === false);
              else if (op === 'not_exists') passed = (actual === undefined || actual === null || actual === '' || actual === false);
              else if (op === 'contains') passed = String(actual).indexOf(expect.value) !== -1;
              else passed = false;
              if (!passed) return false;
            }
            return true;
          };
          const collect = () => {
            const values = {};
            for (const name in page_features) values[name] = evalFeat(name, page_features[name]);
            return values;
          };
          const values = collect();
          if (checkPass(values)) return {status: 'pass', values: values};
          return await new Promise((resolve) => {
            let settled = false;
            const finish = (status) => { if (settled) return; settled = true; resolve({status: status, values: collect()}); };
            // 页面开始导航(整页跳转)时立即返回 'navigated'——Promise 在旧 context
            // 会被销毁，与其等 timeout 不如立刻让 Python 端 fallback poll 到新页面。
            const onNav = () => { if (mo) mo.disconnect(); finish('navigated'); };
            const mo = new MutationObserver(() => {
              if (checkPass(collect())) finish('pass');
            });
            mo.observe(document.body || document.documentElement,
                       {subtree: true, childList: true, attributes: true, characterData: true});
            window.addEventListener('pagehide', onNav, {once: true});
            window.addEventListener('beforeunload', onNav, {once: true});
            setTimeout(() => { mo.disconnect(); finish('timeout'); }, timeoutMs);
          });
        })()
        """ % (feats, exps, timeout_ms)
        return await self.eval_js(js)
