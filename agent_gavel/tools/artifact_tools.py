"""M3：download_* / artifact_* MCP 工具（docs/tech-plan.md §11）。

产物用 artifact_id 引用；对外**绝不暴露绝对路径**（内部 path 字段剥掉）。
"""

from ..runtime.errors import GavelError
from .common import guard


def _public(info):
    out = dict(info)
    out.pop("path", None)
    return out


def register_artifact_tools(mcp):
    @mcp.tool()
    @guard
    async def artifact_list(kind: str = None, session_id: str = None,
                            run_id: str = None):
        """列出产物（可按 kind/session/run 过滤）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        items = rt.artifacts.list(kind=kind, session_id=session_id,
                                  run_id=run_id)
        return {"status": "ok", "items": [_public(i) for i in items],
                "total": len(items)}

    @mcp.tool()
    @guard
    async def artifact_get(artifact_id: str):
        """取单个产物元数据。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        return {"status": "ok", **_public(rt.artifacts.get(artifact_id))}

    @mcp.tool()
    @guard
    async def artifact_export(artifact_id: str, dest_dir: str,
                              filename: str = None):
        """把产物导出到本地目录（受文件白名单约束）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        res = rt.artifacts.export(artifact_id, dest_dir, filename,
                                  policies=rt.policies)
        return {"status": "ok", **res}

    @mcp.tool()
    @guard
    async def artifact_cleanup(ttl_days: int = None, max_bytes: int = None):
        """按 TTL/总量清理产物（缺省用 policy 配置）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        ttl = ttl_days if ttl_days is not None else rt.policies.artifact_ttl_days
        cap = max_bytes if max_bytes is not None \
            else rt.policies.artifact_max_bytes
        return {"status": "ok", **rt.artifacts.cleanup(ttl_days=ttl,
                                                       max_bytes=cap)}

    @mcp.tool()
    @guard
    async def download_list(page_id: str = None, session_id: str = None):
        """列出已捕获的下载。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        items = rt.artifacts.list(kind="download", session_id=session_id)
        if page_id:
            items = [i for i in items if i.get("page_id") == page_id]
        return {"status": "ok", "items": [_public(i) for i in items],
                "total": len(items)}

    @mcp.tool()
    @guard
    async def download_get(artifact_id: str):
        """取单个下载的元数据。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        info = rt.artifacts.get(artifact_id)
        if info.get("kind") != "download":
            raise GavelError("not_a_download", f"{artifact_id} 不是下载产物")
        return {"status": "ok", **_public(info)}

    @mcp.tool()
    @guard
    async def download_save(artifact_id: str, dest_dir: str,
                            filename: str = None):
        """把下载保存到本地目录（受文件白名单约束）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        info = rt.artifacts.get(artifact_id)
        res = rt.artifacts.export(artifact_id, dest_dir,
                                  filename or info.get("suggested_filename"),
                                  policies=rt.policies)
        return {"status": "ok", **res}

    @mcp.tool()
    @guard
    async def download_read_text(artifact_id: str):
        """抽取下载文档的文本（PDF/Word/文本）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        return {"status": "ok", **rt.artifacts.read_text(artifact_id)}

    @mcp.tool()
    @guard
    async def download_delete(artifact_id: str):
        """删除一个产物。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        return {"status": "ok", **rt.artifacts.delete(artifact_id)}
