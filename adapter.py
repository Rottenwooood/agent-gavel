"""computer-use-linux MCP client 适配器。

方案 A：本 server 作为 MCP client 连接 computer-use-linux 的 stdio
MCP server，把它的底层工具封装成我们闭环原语用的原子操作。

只暴露我们需要的几个方法：
  read_state(窗口选择器) -> 原始节点列表（喂给 normalize）
  click / type_text / press_key / scroll / activate_window
  screenshot(窗口选择器) -> 截图引用

常驻连接：computer-use-linux 子进程只在首次调用时启动（懒加载），
后续复用同一连接，避免每次工具调用都 spawn 子进程（~1s 级开销）。
断线时自动重连。
"""

import asyncio
import json
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _xsel_write(text, selection="clipboard"):
    """用 xsel 把文本写入 X11 剪贴板（无 xsel 则跳过，留给调用方报错）。"""
    import os
    import shutil
    import subprocess

    if shutil.which("xsel") is None:
        return {"ok": False, "error": "xsel not found"}
    env = dict(os.environ)
    if "DISPLAY" not in env or not env.get("DISPLAY"):
        env["DISPLAY"] = ":1"  # X11 会话兜底
    try:
        p = subprocess.run(
            ["xsel", "--%s" % selection, "--input"],
            input=text.encode("utf-8"), capture_output=True, timeout=5, env=env)
        return {"ok": p.returncode == 0, "error": p.stderr.decode("utf-8", "replace")[:200]
                if p.returncode else None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


class ComputerUseClient:
    """常驻 computer-use-linux MCP 客户端。

    独立后台线程跑自己的 asyncio 事件循环，该循环内创建并持有
    computer-use-linux 的 stdio 连接。宿主进程（agent-gavel，同为
    anyio/asyncio MCP server）通过 run_coroutine_threadsafe 投递调用，
    两套事件循环彻底隔离，避免 anyio cancel-scope 交叉导致跨调用崩溃。

    断线时在后台线程内自动重连。
    """

    def __init__(self, command=None, args=None):
        import os
        # 可用环境变量 COMPUTER_USE_LINUX_BIN 覆盖二进制路径(便于测试优化版)
        self._command = command or os.environ.get(
            "COMPUTER_USE_LINUX_BIN", "computer-use-linux")
        self._args = list(args) if args is not None else ["mcp"]
        self._loop = None          # 后台线程内的事件循环
        self._thread = None        # 后台线程
        self._ready = threading.Event()   # 首次连接完成信号
        self._last_error = None

    # ---- 生命周期（在调用方 asyncio 侧）----

    async def start(self):
        """确保后台线程 + computer-use-linux 连接就绪。"""
        if self._loop is not None and self._thread and self._thread.is_alive():
            # 已启动：确认连接没死（后台 _check 会在断线时标记，简单起见惰性重连）
            return
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._thread_main, args=(loop,), daemon=True)
        self._loop = loop
        self._thread = t
        self._ready.clear()
        self._last_error = None
        t.start()
        # 等待后台线程完成连接初始化
        await asyncio.to_thread(self._ready.wait, 15.0)
        if self._last_error is not None:
            raise RuntimeError(f"computer-use-linux connect failed: {self._last_error}")
        if not self._ready.is_set():
            raise RuntimeError("computer-use-linux connect timeout")

    async def close(self):
        """关闭后台线程与连接（进程退出时）。"""
        loop = self._loop
        self._loop = None
        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                fut.result(timeout=5)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        self._thread = None

    # ---- 后台线程主体 ----

    def _thread_main(self, loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_loop())
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _run_loop(self):
        # 生命周期一直运行，直到收到 shutdown 信号
        try:
            await self._connect()
            self._ready.set()
        except Exception as e:  # noqa: BLE001
            self._last_error = str(e)
            self._ready.set()
            return
        await self._shutdown_event().wait()
        await self._disconnect()

    def _shutdown_event(self):
        if not hasattr(self, "_stop_ev"):
            self._stop_ev = asyncio.Event()
        return self._stop_ev

    async def _shutdown(self):
        try:
            self._shutdown_event().set()
        except Exception:
            pass
        # 给循环一个机会收尾（session/stack 退出）
        try:
            await asyncio.sleep(0.3)
        except Exception:
            pass

    async def _connect(self):
        params = StdioServerParameters(command=self._command, args=self._args)
        self._stack = stdio_client(params)
        self._read, self._write = await self._stack.__aenter__()
        self._session = await ClientSession(self._read, self._write).__aenter__()
        await self._session.initialize()
        self._lock = asyncio.Lock()

    async def _disconnect(self):
        try:
            if self._session is not None:
                await self._session.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        try:
            if self._stack is not None:
                await self._stack.__aexit__(None, None, None)
        except Exception:
            pass
        self._stack = None

    # ---- 调用桥接 ----

    async def _call(self, tool, args=None):
        """在调用方 asyncio 侧调用；投递到后台线程循环执行。"""
        if self._loop is None or not (self._thread and self._thread.is_alive()):
            await self.start()
        loop = self._loop
        fut = asyncio.run_coroutine_threadsafe(self._call_in_loop(tool, args or {}), loop)
        return await asyncio.wrap_future(fut)

    async def _call_in_loop(self, tool, args):
        """在后台线程循环内执行；断线时自动重连重试一次。"""
        if self._session is None:
            await self._connect()
            self._lock = asyncio.Lock()
        async with self._lock:
            try:
                result = await self._session.call_tool(tool, args)
            except Exception:
                # 连接可能已断。重连后重试一次。
                await self._disconnect()
                await self._connect()
                result = await self._session.call_tool(tool, args)
            text_parts = []
            for part in result.content:
                if getattr(part, "type", None) == "text":
                    text_parts.append(part.text)
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            raw = "\n".join(text_parts)
            try:
                return json.loads(raw)
            except Exception:
                return raw

    async def read_state(self, app_id=None, fast_app_filter=None):
        """读当前窗口/应用的 AT-SPI 树，返回原始节点列表。

        get_app_state 返回 {accessibility_tree, screenshot, window_context...}
        我们取 accessibility_tree 字段喂给 normalize。

        fast_app_filter: True 走改版 computer-use-linux 的快速 app-filter 路径
        (有 pid 时跳过全桌面遍历定位, ~快 270ms); None=读环境变量
        AGENT_GAVEL_FAST_APP_FILTER(默认 false, 保持原版行为)。
        """
        import os
        if fast_app_filter is None:
            fast_app_filter = os.environ.get("AGENT_GAVEL_FAST_APP_FILTER", "0") in (
                "1", "true", "True")
        args = {}
        if app_id:
            args["app_id"] = app_id
        if fast_app_filter is not None:
            args["fast_app_filter"] = bool(fast_app_filter)
        args["include_screenshot"] = False
        args["max_depth"] = 16
        args["max_nodes"] = 2000
        data = await self._call("get_app_state", args)
        if isinstance(data, dict):
            return data.get("accessibility_tree") or data.get("accessibility_tree_raw") or []
        return data

    async def click(self, *, element_index=None, role=None, name=None, text=None,
                    app_id=None, x=None, y=None, button="left", window_id=None):
        args = {}
        if element_index is not None:
            args["element_index"] = element_index
        if role:
            args["role"] = role
        if name:
            args["name"] = name
        if text:
            args["text"] = text
        if app_id:
            args["app_id"] = app_id
        if window_id:
            args["window_id"] = window_id
        if x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        if button:
            args["button"] = button
        return await self._call("click", args)

    async def type_text(self, text, *, app_id=None, method=None, window_id=None):
        """输入文本到焦点窗口。

        method:
          "keys"      - 逐键模拟（xdotool/portal），ASCII 可靠
          "clipboard" - 写 X 剪贴板 + Ctrl+V，任何文本（含中文）都可靠
          None        - 自动：含非 ASCII 时用 clipboard，否则 keys

        注意：clipboard 的 Ctrl+V 故意**不带 app_id**——computer-use-linux
        对带 app_id 的按键会先查 AT-SPI 焦点，微信这类 Electron/混合应用
        的输入框 AT-SPI 焦点常不可见，会被误拦。靠 window_id 定位到窗口后
        Ctrl+V 走 X 焦点粘贴，可靠。调用方应先 activate/click 输入框。
        """
        import asyncio as _aio

        text = text or ""
        non_ascii = any(ord(ch) > 127 for ch in text)
        use_clip = method == "clipboard" or (method is None and non_ascii)
        if use_clip:
            # 只写 CLIPBOARD：实测微信(Ctrl+V)读的是 CLIPBOARD，不读 PRIMARY。
            # 勿同时写 PRIMARY——两次 xsel 写入会触发 X 剪贴板归属竞争，
            # 微信可能粘到旧的 CLIPBOARD 缓存。
            wr = await _aio.to_thread(_xsel_write, text, "clipboard")
            if not wr.get("ok"):
                return {"method": "clipboard", "ok": False,
                        "error": f"xsel write failed: {wr.get('error')}"}
            # 等 X 剪贴板 owner 完成归属切换（写后立即粘贴偶发读旧值）
            await _aio.sleep(0.3)
            # 粘贴到当前 X 焦点（用 window_id 定位即可，不带 app_id 避免焦点校验拦截）
            args = {"key": "Ctrl+V"}
            if window_id:
                args["window_id"] = window_id
            res = await self._call("press_key", args)
            return {"method": "clipboard", "ok": True, "text_len": len(text),
                    "raw": res if not isinstance(res, dict) else res.get("message", res)}
        args = {"text": text}
        if app_id:
            args["app_id"] = app_id
        res = await self._call("type_text", args)
        return {"method": "keys", "ok": True, "raw": res if not isinstance(res, dict) else res.get("message", res)}

    async def press_key(self, key, *, app_id=None):
        args = {"key": key}
        if app_id:
            args["app_id"] = app_id
        return await self._call("press_key", args)

    async def scroll(self, direction, pages=1.0, *, app_id=None):
        args = {"direction": direction, "pages": pages}
        if app_id:
            args["app_id"] = app_id
        return await self._call("scroll", args)

    async def activate_window(self, *, title=None, app_id=None, pid=None, wm_class=None):
        args = {}
        if title:
            args["title"] = title
        if app_id:
            args["app_id"] = app_id
        if pid is not None:
            args["pid"] = pid
        if wm_class:
            args["wm_class"] = wm_class
        return await self._call("activate_window", args)

    async def screenshot(self, *, app_id=None, format="jpeg", quality=70):
        args = {"format": format, "quality": quality}
        if app_id:
            args["app_id"] = app_id
        return await self._call("screenshot", args)

    async def move_window(self, *, window_id=None, x=None, y=None):
        args = {}
        if window_id:
            args["window_id"] = window_id
        if x is not None:
            args["x"] = x
        if y is not None:
            args["y"] = y
        return await self._call("move_window", args)

    async def list_windows(self):
        return await self._call("list_windows")

    async def __aenter__(self):
        """context manager：返回全局常驻单例（懒启动复用）。

        所有 'async with ComputerUseClient() as client' 拿到的是同一实例，
        子进程只 spawn 一次；调用方代码无需感知常驻细节。
        """
        res = await get_client()
        return res

    async def __aexit__(self, *exc):
        # 常驻连接不随单个工具调用退出；这里无操作（close 由 server 退出时统一做）
        return None


# ---- 模块级常驻单例 ----

_resident = None
_resident_lock = asyncio.Lock()


async def get_client():
    """获取常驻 computer-use-linux client（懒启动，复用连接，断线自愈）。"""
    global _resident
    if _resident is not None:
        return _resident
    async with _resident_lock:
        if _resident is None:
            _resident = ComputerUseClient()
            await _resident.start()
    return _resident


async def close_resident():
    """关闭常驻连接（agent-gavel server 退出前调用）。"""
    global _resident
    if _resident is not None:
        await _resident.close()
        _resident = None
