"""M4：trace / network / console / errors / metrics MCP 工具（docs/tech-plan.md §8）。"""

import os
import tempfile

from ..runtime.errors import GavelError
from .common import guard


def register_diagnostics_tools(mcp):
    @mcp.tool()
    @guard
    async def trace_start(context_id: str):
        """对 context 开启 Playwright trace（截图+snapshot+source）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        ctx = rt.context_mgr.get(context_id)
        await rt.tracer.start(ctx)
        return {"status": "ok", "context_id": context_id, "tracing": True}

    @mcp.tool()
    @guard
    async def trace_stop(context_id: str):
        """停止 trace 并存成 artifact（zip）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        ctx = rt.context_mgr.get(context_id)
        if not rt.tracer.is_active(context_id):
            raise GavelError("trace_not_active", "该 context 未开启 trace",
                             hint="先 trace_start")
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        await rt.tracer.stop(ctx, path)
        info = rt.artifacts.register_file(
            path, filename=f"{context_id}.trace.zip", kind="trace",
            mime_type="application/zip")
        return {"status": "ok", "context_id": context_id,
                "artifact_id": info["artifact_id"],
                "size_bytes": info["size_bytes"]}

    @mcp.tool()
    @guard
    async def trace_get(artifact_id: str):
        """取 trace artifact 元数据。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        info = rt.artifacts.get(artifact_id)
        info.pop("path", None)
        return {"status": "ok", **info}

    @mcp.tool()
    @guard
    async def network_start(context_id: str):
        """开始记录 context 的网络请求/响应。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        rt.context_mgr.get(context_id)  # 校验存在
        return {"status": "ok", **rt.network.start(context_id)}

    @mcp.tool()
    @guard
    async def network_stop(context_id: str, limit: int = 1000):
        """停止网络记录并返回记录列表。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        return {"status": "ok", **rt.network.stop(context_id, limit=limit)}

    @mcp.tool()
    @guard
    async def console_get(page_id: str = None, limit: int = 100):
        """取页面 console error/warning 事件。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        items = rt.events.recent(kinds=["console_error", "console_warning"],
                                 limit=1000)
        if page_id:
            items = [e for e in items if e.get("page_id") == page_id]
        return {"status": "ok", "items": items[-limit:], "total": len(items)}

    @mcp.tool()
    @guard
    async def page_errors(page_id: str = None, limit: int = 100):
        """取页面 JS 错误/崩溃事件。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        items = rt.events.recent(kinds=["page_error", "page_crashed"],
                                 limit=1000)
        if page_id:
            items = [e for e in items if e.get("page_id") == page_id]
        return {"status": "ok", "items": items[-limit:], "total": len(items)}

    @mcp.tool()
    @guard
    async def performance_metrics():
        """返回各动作类型的 p50/p95/p99 延迟指标。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        return {"status": "ok", "metrics": rt.metrics.snapshot()}
