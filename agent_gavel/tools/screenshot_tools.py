"""Capture once, store once, return native image content and reusable metadata."""

import asyncio

from ..core.errors import CAPTURE_FAILED, GavelError
from ..core.results import Image, ToolReply
from ..runtime import get_runtime
from .common import backend, ids


def register_screenshot_tools(catalog):
    @catalog.operation(returns="Image")
    async def screenshot(
        page_id: str = None, selector: str = None, full_page: bool = False, save_to: str = None
    ):
        """直接返回PNG图片；selector优先截元素，否则截视口/整页；save_to为导出目录。"""
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        captured = await handle.actor.submit(
            "screenshot", lambda: be.screenshot(handle, full_page=full_page, selector=selector)
        )
        if not captured["width"] or not captured["height"]:
            raise GavelError(CAPTURE_FAILED, "截图未返回有效PNG尺寸")
        info = await asyncio.to_thread(
            rt.artifacts.save_bytes,
            captured["data"],
            filename=f"{handle.page_id}.png",
            kind="screenshot",
            mime_type="image/png",
            source_url=captured["url"],
            session_id=handle.context.lease_session_id(),
            page_id=handle.page_id,
        )
        result = {
            "status": "ok",
            **ids(handle),
            "artifact_id": info["artifact_id"],
            "width": captured["width"],
            "height": captured["height"],
            "device_scale_factor": captured["device_scale_factor"],
        }
        if save_to:
            exported = await asyncio.to_thread(
                rt.artifacts.export, info["artifact_id"], save_to, policies=rt.policies
            )
            result.update(exported=True, exported_filename=exported["filename"])
        return ToolReply(result, [Image(captured["data"])])
