"""M4：workflow_* MCP 工具（docs/tech-plan.md §8、§5.15）。"""

from ..core.errors import GavelError
from .common import backend


def _build_tid(site_slug, name_slug):
    """拼 template_id；若 name 已含 site 前缀则不重复拼。"""
    if site_slug and name_slug.startswith(site_slug):
        return name_slug
    return "_".join(x for x in (site_slug, name_slug) if x) or "recorded_workflow"


def _runner():
    from ..runtime import get_runtime
    from ..workflows.runner import Runner

    rt = get_runtime()
    return rt, Runner(rt, backend())


def _find_run(rt, run_id):
    if run_id:
        st = rt.runs.get(run_id)
        if st is None:
            raise GavelError("run_not_found", f"未知 run：{run_id}")
        return st
    if not rt.runs:
        raise GavelError("no_run", "当前没有运行中的 workflow")
    return list(rt.runs.values())[-1]


def register_workflow_tools(catalog):
    @catalog.operation(returns="WorkflowSaved", required=("template",), nonempty=("template",))
    async def workflow_save(template: dict = None):
        "校验后保存，同id版本递增。"
        from ..workflows import loader
        from ..workflows import schema as S

        if not template:
            raise GavelError("bad_args", "需要 template")
        norm, errors, warnings = S.validate(template)
        if errors:
            return {
                "status": "error",
                "reason": "invalid_template",
                "errors": errors,
                "warnings": warnings,
            }
        info = loader.save(norm)
        return {"status": "ok", **info, "warnings": warnings}

    @catalog.operation(returns="WorkflowDeleted")
    async def workflow_delete(template_id: str):
        "只接受精确template_id。"
        from ..workflows import loader

        return {"status": "ok", **loader.delete(template_id)}

    @catalog.operation(returns="Validation", required=("template",), nonempty=("template",))
    async def workflow_validate(template: dict = None):
        "校验模板，返回是否有效及错误/告警。"
        from ..workflows import schema as S

        if not template:
            raise GavelError("bad_args", "需要 template")
        norm, errors, warnings = S.validate(template)
        return {
            "status": "ok",
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "normalized": norm if not errors else None,
        }

    @catalog.operation(returns="Workflows")
    async def workflow_list():
        """"""
        from ..workflows import loader

        return {"status": "ok", "items": loader.list_workflows()}

    @catalog.operation(returns="Template")
    async def workflow_get(name_or_id: str):
        "按精确id或site只读查询。"
        from ..workflows import loader

        t, _path = loader.load(name_or_id)
        return {"status": "ok", "template": t}

    @catalog.operation(returns="Published")
    async def workflow_publish(template_id: str, out_dir: str = None):
        "导出模板，不联网。"
        from ..workflows import loader

        return {"status": "ok", **loader.publish(template_id, out_dir)}

    @catalog.operation(returns="WorkflowStats")
    async def workflow_stats():
        """"""
        from ..workflows import loader

        return {"status": "ok", "stats": loader.stats()}

    @catalog.operation(returns="Recording")
    async def workflow_record_start(
        context_id: str = None, page_id: str = None, keep_all: bool = False
    ):
        "录制后续元素动作和导航。"
        from ..runtime import get_runtime

        rt = get_runtime()
        h = rt.pages.get(page_id) if page_id else None
        if h is None and rt.pages:
            try:
                h = rt.page_mgr.resolve()
            except Exception:
                h = None
        info = rt.recorder.start(
            context_id=context_id or (h.context_id if h else None),
            page_id=page_id or (h.page_id if h else None),
            keep_all=keep_all,
        )
        return {"status": "ok", **info}

    @catalog.operation(returns="WorkflowSaved")
    async def workflow_record_stop(
        site: str = None,
        desc: str = "",
        name: str = None,
        template_id: str = None,
        parameterize: dict = None,
        sensitive: list = None,
        auto_checkpoint: bool = False,
    ):
        "编译保存草稿；parameterize={变量:被替换值}。"
        from ..runtime import get_runtime
        from ..workflows import compiler, loader

        rt = get_runtime()
        rec = rt.recorder.stop()
        if rec is None:
            raise GavelError("not_recording", "当前没有在录制")
        tid = template_id or _build_tid(
            loader.slugify(site) if site else "", loader.slugify(name) if name else ""
        )
        draft, errors, warnings = compiler.compile(
            rec.steps,
            template_id=tid,
            site=site or "",
            desc=desc,
            parameterize=parameterize,
            sensitive=sensitive,
            auto_checkpoint=auto_checkpoint,
        )
        if errors:
            return {
                "status": "error",
                "reason": "compile_failed",
                "errors": errors,
                "warnings": warnings,
                "recorded_steps": len(rec.steps),
            }
        info = loader.save(draft)
        return {
            "status": "ok",
            **info,
            "warnings": warnings,
            "recorded_steps": len(rec.steps),
            "template": draft,
        }

    @catalog.operation(
        returns="WorkflowRun", fields={"verbosity": {"enum": ["summary", "steps", "full"]}}
    )
    async def workflow_run(
        name_or_id: str,
        params: dict = None,
        context_id: str = None,
        verbosity: str = "summary",
        run_id: str = None,
        confirm: bool = False,
        wait_s: float = 6.0,
        page_id: str = None,
    ):
        "失败步含证据，确认步骤需confirm。"
        from ..workflows import loader

        rt, runner = _runner()
        template, _path = loader.load(name_or_id)
        result = await runner.run(
            template,
            params or {},
            context_id=context_id,
            verbosity=verbosity,
            run_id=run_id,
            confirm=confirm,
            wait_s=wait_s,
            page_id=page_id,
        )
        # 局部修复只产出建议；要写回就改 JSON 再 workflow_save。
        return result

    @catalog.operation(returns="RunControl")
    async def workflow_pause(run_id: str = None):
        "在步边界暂停。"
        from ..runtime import get_runtime

        st = _find_run(get_runtime(), run_id)
        st.pause_event.clear()
        st.status = "paused"
        return {"status": "ok", "run_id": st.run_id, "paused": True}

    @catalog.operation(returns="RunControl")
    async def workflow_resume(run_id: str = None):
        "暂停态继续；失败态从失败步续跑（不重放已完成步骤）。"
        rt, runner = _runner()
        st = _find_run(rt, run_id)
        if st.status in ("fail", "ambiguous", "error"):
            return await runner.resume(st.run_id)
        st.pause_event.set()
        if st.status == "paused":
            st.status = "running"
        return {"status": "ok", "run_id": st.run_id, "paused": False}

    @catalog.operation(returns="RunControl")
    async def workflow_cancel(run_id: str = None):
        "在步边界取消。"
        from ..runtime import get_runtime

        st = _find_run(get_runtime(), run_id)
        st.cancelled = True
        st.pause_event.set()
        return {"status": "ok", "run_id": st.run_id, "cancelled": True}
