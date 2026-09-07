"""computer-use-linux MCP client 适配器。

方案 A：本 server 作为 MCP client 连接 computer-use-linux 的 stdio
MCP server，把它的底层工具封装成我们闭环原语用的原子操作。

只暴露我们需要的几个方法：
  read_state(窗口选择器) -> 原始节点列表（喂给 normalize）
  click / type_text / press_key / scroll / activate_window
  screenshot(窗口选择器) -> 截图引用
"""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ComputerUseClient:
    def __init__(self, command="computer-use-linux", args=("mcp",)):
        self._command = command
        self._args = list(args)
        self._session = None
        self._stack = None

    async def __aenter__(self):
        params = StdioServerParameters(command=self._command, args=self._args)
        self._stack = stdio_client(params)
        self._read, self._write = await self._stack.__aenter__()
        self._session = await ClientSession(self._read, self._write).__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._stack.__aexit__(*exc)

    async def _call(self, tool, args=None):
        if self._session is None:
            raise RuntimeError("client not started")
        result = await self._session.call_tool(tool, args or {})
        # result.content 是 list[TextContent | ImageContent]
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

    async def read_state(self, app_id=None):
        """读当前窗口/应用的 AT-SPI 树，返回原始节点列表。

        get_app_state 返回 {accessibility_tree, screenshot, window_context...}
        我们取 accessibility_tree 字段喂给 normalize。
        """
        args = {}
        if app_id:
            args["app_id"] = app_id
        args["include_screenshot"] = False
        args["max_depth"] = 8
        args["max_nodes"] = 2000
        data = await self._call("get_app_state", args)
        if isinstance(data, dict):
            return data.get("accessibility_tree") or data.get("accessibility_tree_raw") or []
        return data

    async def click(self, *, element_index=None, role=None, name=None, text=None,
                    app_id=None, x=None, y=None, button="left"):
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
        if x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        if button:
            args["button"] = button
        return await self._call("click", args)

    async def type_text(self, text, *, app_id=None):
        args = {"text": text}
        if app_id:
            args["app_id"] = app_id
        return await self._call("type_text", args)

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

    async def list_windows(self):
        return await self._call("list_windows")
