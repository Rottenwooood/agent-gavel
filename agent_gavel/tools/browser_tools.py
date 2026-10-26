"""浏览器进程工具：启动、状态、关闭。"""

from ..core import config
from ..runtime import get_runtime
from .common import ATTACH_WARNING as _ATTACH_WARNING
from .common import guard


def register_browser_tools(mcp):
    rt_get = get_runtime

    @mcp.tool()
    @guard
    async def browser_launch(
        headless: bool = None,
        channel: str = None,
        proxy: dict = None,
        browser_mode: str = None,
        cdp_endpoint: str = None,
    ):
        """启动/确保默认浏览器进程。

        默认 **managed（C1）**：自起加固 Chrome（持久真实资料目录）+ Playwright 连接；
        `browser_mode="attach"` + `cdp_endpoint` 则**接管**一个已存在的浏览器（C2，见警告）；
        `browser_mode="playwright"` 回到 Playwright 直接启动。
        headless 省略时按 AGENT_GAVEL_HEADLESS 决定（默认有头）。

        ⚠ 隐私敏感：`browser_mode="attach"`（C2）会操作**用户真实账号环境**，并把控制权
        暴露在本机调试口。使用前必须先向用户说明并取得同意。
        """
        rt = rt_get()
        handle = await rt.ensure_browser(
            headless=headless,
            channel=channel,
            proxy=proxy,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
        )
        out = {"status": "ok", **handle.to_dict(), "counts": (await rt.status())["counts"]}
        attach = (
            handle.mode == "attach"
            or (browser_mode or config.browser_mode()).strip().lower() == "attach"
            or bool(cdp_endpoint or config.cdp_endpoint())
        )
        if attach:
            out["warning"] = _ATTACH_WARNING
        return out

    @mcp.tool()
    @guard
    async def browser_status():
        """只读：当前进程/session/context/page 计数、事件与指标快照。"""
        rt = rt_get()
        return {"status": "ok", **(await rt.status())}

    @mcp.tool()
    @guard
    async def browser_close():
        """关闭所有浏览器进程与资源（runtime 仍可继续用，下次调用会重拉）。"""
        rt = rt_get()
        await rt.shutdown()
        rt.mark_open()
        return {"status": "ok", "closed": True}
