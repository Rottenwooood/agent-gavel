"""M3：download_* / artifact_* MCP 工具（docs/tech-plan.md §11）。

产物用 artifact_id 引用；对外**绝不暴露绝对路径**（内部 path 字段剥掉）。
"""

import asyncio


def _public(info):
    out = dict(info)
    out.pop("path", None)
    return out


def register_artifact_tools(catalog):
    @catalog.operation(returns="Artifacts")
    async def artifact_list(
        kind: str = None, context_id: str = None, run_id: str = None, page_id: str = None
    ):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        items = rt.artifacts.list(kind=kind, context_id=context_id, run_id=run_id, page_id=page_id)
        return {"status": "ok", "items": [_public(i) for i in items], "total": len(items)}

    @catalog.operation(returns="Artifact")
    async def artifact_get(artifact_id: str, expected_kind: str = None):
        "expected_kind用于类型检查。"
        from ..runtime import get_runtime

        rt = get_runtime()
        return {
            "status": "ok",
            **_public(rt.artifacts.get(artifact_id, expected_kind=expected_kind)),
        }

    @catalog.operation(
        returns="Export", fields={"path_visibility": {"enum": ["hidden", "user_visible"]}}
    )
    async def artifact_export(
        artifact_id: str, dest_dir: str, filename: str = None, path_visibility: str = "hidden"
    ):
        "dest_dir为目录；缺省filename取建议名；user_visible才回绝对路径。"
        from ..runtime import get_runtime

        rt = get_runtime()
        res = await asyncio.to_thread(
            rt.artifacts.export,
            artifact_id,
            dest_dir,
            filename,
            policies=rt.policies,
            path_visibility=path_visibility,
        )
        return {"status": "ok", **res}

    @catalog.operation(returns="Cleanup")
    async def artifact_cleanup(ttl_days: int = None, max_bytes: int = None):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        ttl = ttl_days if ttl_days is not None else rt.policies.artifact_ttl_days
        cap = max_bytes if max_bytes is not None else rt.policies.artifact_max_bytes
        res = await asyncio.to_thread(rt.artifacts.cleanup, ttl_days=ttl, max_bytes=cap)
        return {"status": "ok", **res}

    @catalog.operation(returns="Extracted")
    async def artifact_read_text(artifact_id: str):
        """抽取产物文本，支持PDF/Word/文本；其他类型返回extracted=false。"""
        from ..runtime import get_runtime

        return {
            "status": "ok",
            **await asyncio.to_thread(get_runtime().artifacts.read_text, artifact_id),
        }

    @catalog.operation(returns="Deleted")
    async def artifact_delete(artifact_id: str):
        """删除指定产物。"""
        from ..runtime import get_runtime

        return {
            "status": "ok",
            **await asyncio.to_thread(get_runtime().artifacts.delete, artifact_id),
        }
