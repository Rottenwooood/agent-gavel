"""daemon 生命周期工具。"""

import os

from ..core.errors import GavelError
from .common import guard


def register_daemon_tools(mcp):
    @mcp.tool()
    @guard
    async def daemon_shutdown(confirm: bool = False, force: bool = False):
        """停止当前 daemon（会关闭所有浏览器、session 与页面）。

        confirm=True 才会执行；有运行中的 workflow 时需要 force=True。
        停止后 adapter 仍存活，下一次调用会自动拉起新的 daemon。
        """
        from ..runtime import get_runtime

        rt = get_runtime()
        if not confirm:
            return {
                "status": "error",
                "reason": "confirmation_required",
                "error": "daemon_shutdown 会关闭所有浏览器与会话；确认后传 confirm=True",
            }
        if rt.runs and not force:
            return {
                "status": "error",
                "reason": "daemon_busy",
                "detail": {"runs": list(rt.runs)},
                "hint": "等待运行结束，或 force=True 强制停止",
            }
        hook = getattr(rt, "shutdown_hook", None)
        if hook is None:
            raise GavelError("not_daemon", "当前进程不是 daemon")
        hook()
        return {"status": "ok", "stopping": True, "daemon_pid": os.getpid()}
