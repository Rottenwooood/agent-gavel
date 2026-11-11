"""M4：trace / network / console / errors / metrics MCP 工具（docs/tech-plan.md §8）。"""

import asyncio
import os
import tempfile

from ..core.errors import GavelError


def register_diagnostics_tools(catalog):
    @catalog.operation(returns="Tracing")
    async def trace_start(context_id: str):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        ctx = rt.context_mgr.get(context_id)
        await rt.tracer.start(ctx)
        return {"status": "ok", "context_id": context_id, "tracing": True}

    @catalog.operation(returns="TraceArtifact")
    async def trace_stop(context_id: str):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        ctx = rt.context_mgr.get(context_id)
        if not rt.tracer.is_active(context_id):
            raise GavelError("trace_not_active", "该 context 未开启 trace", hint="先 trace_start")
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        await rt.tracer.stop(ctx, path)
        info = await asyncio.to_thread(
            rt.artifacts.register_file,
            path,
            filename=f"{context_id}.trace.zip",
            kind="trace",
            mime_type="application/zip",
        )
        return {
            "status": "ok",
            "context_id": context_id,
            "artifact_id": info["artifact_id"],
            "size_bytes": info["size_bytes"],
        }

    @catalog.operation(returns="Network")
    async def network_start(context_id: str):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        rt.context_mgr.get(context_id)  # 校验存在
        return {"status": "ok", **rt.network.start(context_id)}

    @catalog.operation(returns="NetworkRecords")
    async def network_stop(context_id: str, limit: int = 1000):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        return {"status": "ok", **rt.network.stop(context_id, limit=limit)}

    @catalog.operation(returns="Events")
    async def console_get(page_id: str = None, limit: int = 100):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        items = rt.events.recent(kinds=["console_error", "console_warning"], limit=1000)
        if page_id:
            items = [e for e in items if e.get("page_id") == page_id]
        return {"status": "ok", "items": items[-limit:], "total": len(items)}

    @catalog.operation(returns="Events")
    async def page_errors(page_id: str = None, limit: int = 100):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        items = rt.events.recent(kinds=["page_error", "page_crashed"], limit=1000)
        if page_id:
            items = [e for e in items if e.get("page_id") == page_id]
        return {"status": "ok", "items": items[-limit:], "total": len(items)}

    @catalog.operation(returns="Metrics")
    async def performance_metrics():
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        return {"status": "ok", "metrics": rt.metrics.snapshot()}
