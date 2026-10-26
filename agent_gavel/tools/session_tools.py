"""会话工具。"""

import asyncio
import pathlib

from ..core.errors import GavelError
from ..runtime import get_runtime
from .common import ATTACH_WARNING as _ATTACH_WARNING
from .common import guard


def register_session_tools(mcp):
    rt_get = get_runtime

    @mcp.tool()
    @guard
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
        """创建隔离 session（ephemeral / persistent / clone）。

        container（容器策略）：
          default（默认）= 用资料目录的**默认容器**：继承 profile 的登录/cookie，
                  改动累积回 profile（可迭代）。
          isolated      = 新建隔离容器：继承 profile 的 cookie，但不写回 profile。
        ephemeral：共享默认浏览器上建容器。persistent：独立进程 + 持久资料目录。
        clone：从 storage_state 起（默认用 isolated 容器，适合多账号）。
        browser_mode="attach" + cdp_endpoint：接管一个已存在浏览器（C2，见返回 warning）。

        ⚠ 隐私敏感：`browser_mode="attach"` 会操作用户真实账号环境；`user_data_dir` 若指向
        用户日常资料目录、或 `container="default"`/`persistent` 会读写其真实登录态。
        这些用法的调用前必须先向用户说明并取得同意；不需要登录态时用 `container="isolated"`
        并避免指向用户资料目录。
        """
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

    @mcp.tool()
    @guard
    async def session_list():
        """列出所有 session。"""
        rt = rt_get()
        return {"status": "ok", "items": [s.to_dict() for s in rt.sessions.values()]}

    @mcp.tool()
    @guard
    async def session_get(session_id: str):
        """取单个 session 详情（含 contexts / busy 状态）。"""
        rt = rt_get()
        session = rt.session_mgr.get(session_id)
        out = {
            "status": "ok",
            **session.to_dict(),
            "contexts": [rt.contexts[c].to_dict() for c in session.context_ids if c in rt.contexts],
        }
        return out

    @mcp.tool()
    @guard
    async def session_reset(session_id: str):
        """按原配置重建 session（关旧开新，保留 mode 与 config）。"""
        rt = rt_get()
        session = await rt.session_mgr.reset(session_id)
        return {"status": "ok", **session.to_dict()}

    @mcp.tool()
    @guard
    async def session_close(session_id: str):
        """关闭 session 及其全部 context/page。"""
        rt = rt_get()
        return {"status": "ok", **await rt.session_mgr.close(session_id)}

    @mcp.tool()
    @guard
    async def session_export_state(session_id: str, path: str = None):
        """导出 storage state（cookie/localStorage）。

        给了 path 则写文件（受文件白名单约束）；否则直接返回 state。

        ⚠ 隐私敏感：storage state 含登录态。写文件前确认目标路径，只交给可信位置；
        不要提交到仓库、模板或外传。
        """
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

    @mcp.tool()
    @guard
    async def session_import_state(path: str, mode: str = "clone"):
        """从 storage_state 文件创建新 session（默认 clone 模式）。

        ⚠ 隐私敏感：storage state 含登录态；确认来源可信、属于当前用户。
        """
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
