"""会话工具。"""

import asyncio
import pathlib

from ..core.errors import GavelError
from ..runtime import get_runtime
from ..runtime.sessions import SESSION_MODES
from .common import ATTACH_WARNING as _ATTACH_WARNING


def register_session_tools(catalog):
    rt_get = get_runtime

    @catalog.operation(
        returns="CreatedSession",
        fields={
            "mode": {"enum": list(SESSION_MODES)},
            "container": {"enum": ["default", "isolated", None]},
        },
    )
    async def session_create(
        mode: str = "ephemeral",
        headless: bool = None,
        proxy: dict = None,
        channel: str = None,
        locale: str = None,
        timezone_id: str = None,
        viewport: dict = None,
        device: str = None,
        user_data_dir: str = None,
        storage_state: str = None,
        browser_mode: str = None,
        cdp_endpoint: str = None,
        container: str = None,
    ):
        "ephemeral共享浏览器；persistent独立持久资料；clone由状态文件创建。"
        rt = rt_get()
        config = dict(
            headless=headless,
            proxy=proxy,
            channel=channel,
            locale=locale,
            timezone_id=timezone_id,
            viewport=viewport,
            device=device,
            user_data_dir=user_data_dir,
            storage_state=storage_state,
            browser_mode=browser_mode,
            cdp_endpoint=cdp_endpoint,
            container=container,
        )
        session = await rt.session_mgr.create(mode=mode, **config)
        ctx_id = session.context_ids[0] if session.context_ids else None
        ctx = rt.contexts.get(ctx_id)
        page_id = ctx.active_page_id if ctx else None
        out = {
            "status": "ok",
            **session.to_dict(),
            "context_id": ctx_id,
            "page_id": page_id,
            "shared_container": bool(
                ctx and ctx.container == "default" and len(ctx.session_ids) > 1
            ),
        }
        handle = session.handle
        if handle is not None and handle.mode == "attach":
            out["warning"] = _ATTACH_WARNING
        return out

    @catalog.operation(returns="Sessions")
    async def session_list():
        """"""
        rt = rt_get()
        return {"status": "ok", "items": [s.to_dict() for s in rt.sessions.values()]}

    @catalog.operation(returns="SessionDetail")
    async def session_get(session_id: str):
        """"""
        rt = rt_get()
        session = rt.session_mgr.get(session_id)
        out = {
            "status": "ok",
            **session.to_dict(),
            "contexts": [rt.contexts[c].to_dict() for c in session.context_ids if c in rt.contexts],
        }
        return out

    @catalog.operation(returns="Session")
    async def session_reset(session_id: str):
        """"""
        rt = rt_get()
        session = await rt.session_mgr.reset(session_id)
        return {"status": "ok", **session.to_dict()}

    @catalog.operation(returns="SessionClosed")
    async def session_close(session_id: str):
        "结束租约；默认共享容器只解绑，隔离容器关闭。"
        rt = rt_get()
        return {"status": "ok", **await rt.session_mgr.close(session_id)}

    @catalog.operation(returns="StorageExport")
    async def session_export_state(session_id: str, path: str = None):
        "导出 storage state（cookie/localStorage）。"
        rt = rt_get()
        session = rt.session_mgr.get(session_id)
        if not session.context_ids:
            raise GavelError("context_not_found", "该 session 没有 context")
        ctx = rt.contexts[session.context_ids[0]]
        state = await ctx.browser_context.storage_state()
        if path:
            rt.policies.check_local_path(path, purpose="导出 storage state")
            import json

            payload = json.dumps(state, ensure_ascii=False, indent=2)
            await asyncio.to_thread(pathlib.Path(path).write_text, payload, encoding="utf-8")
            return {"status": "ok", "session_id": session_id, "path": path}
        return {"status": "ok", "session_id": session_id, "storage_state": state}

    @catalog.operation(returns="CreatedSession", fields={"mode": {"enum": list(SESSION_MODES)}})
    async def session_import_state(path: str, mode: str = "clone"):
        "path为可信storage_state文件；mode默认clone。"
        rt = rt_get()
        rt.policies.check_local_path(path, purpose="导入 storage state")
        session = await rt.session_mgr.create(mode=mode, storage_state=path)
        ctx_id = session.context_ids[0] if session.context_ids else None
        ctx = rt.contexts.get(ctx_id)
        return {
            "status": "ok",
            **session.to_dict(),
            "context_id": ctx_id,
            "page_id": ctx.active_page_id if ctx else None,
            "shared_container": bool(
                ctx and ctx.container == "default" and len(ctx.session_ids) > 1
            ),
        }
