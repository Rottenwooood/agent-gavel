"""浏览器进程工具：启动、状态、关闭。"""

from ..core import config
from ..runtime import get_runtime
from .common import ATTACH_WARNING as _ATTACH_WARNING


def register_browser_tools(catalog):
    rt_get = get_runtime

    @catalog.operation(returns="Browser")
    async def browser_launch(
        headless: bool = None,
        channel: str = None,
        proxy: dict = None,
        browser_mode: str = None,
        cdp_endpoint: str = None,
        user_data_dir: str = None,
    ):
        "managed持久Chrome/CDP；attach接管现有；playwright直接启动。"
        rt = rt_get()
        handle = await rt.ensure_browser(
            headless=headless,
            channel=channel,
            proxy=proxy,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            user_data_dir=user_data_dir,
        )
        out = {
            "status": "ok",
            **handle.to_dict(),
            "counts": (await rt.status(detail="compact"))["counts"],
        }
        attach = (
            handle.mode == "attach"
            or (browser_mode or config.browser_mode()).strip().lower() == "attach"
            or bool(cdp_endpoint or config.cdp_endpoint())
        )
        if attach:
            out["warning"] = _ATTACH_WARNING
        return out

    @catalog.operation(returns="BrowserStatus")
    async def browser_status(detail: str = "compact"):
        "资源概况；full再含会话配置、OS进程、事件与指标。"
        rt = rt_get()
        return {"status": "ok", **(await rt.status(detail=detail))}

    @catalog.operation(returns="BrowserClosed")
    async def browser_close(profile: str = None):
        "关闭浏览器进程；给 profile 只关那一个，不给则全关。"
        rt = rt_get()
        if profile:
            closed = await rt.close_profile(profile)
            return {"status": "ok", "closed": bool(closed), "profile": profile}
        await rt.shutdown()
        rt.mark_open()
        return {"status": "ok", "closed": True}
