"""M4：workflow_* MCP 工具（docs/tech-plan.md §8、§5.15）。"""

from ..runtime.errors import GavelError
from .common import backend, guard


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


def register_workflow_tools(mcp):
    @mcp.tool()
    @guard
    async def workflow_save(template: dict = None):
        """校验并保存工作流模板（同 template_id 再存 = version+1）。"""
        from ..workflows import loader
        from ..workflows import schema as S
        if not template:
            raise GavelError("bad_args", "需要 template")
        norm, errors, warnings = S.validate(template)
        if errors:
            return {"status": "error", "reason": "invalid_template",
                    "errors": errors, "warnings": warnings}
        info = loader.save(norm)
        return {"status": "ok", **info, "warnings": warnings}

    @mcp.tool()
    @guard
    async def workflow_validate(template: dict = None):
        """校验模板，返回是否有效及错误/告警。"""
        from ..workflows import schema as S
        if not template:
            raise GavelError("bad_args", "需要 template")
        norm, errors, warnings = S.validate(template)
        return {"status": "ok", "valid": not errors, "errors": errors,
                "warnings": warnings, "normalized": norm if not errors else None}

    @mcp.tool()
    @guard
    async def workflow_list():
        """列出工作流（用户层优先），含版本与成功/失败统计。"""
        from ..workflows import loader
        return {"status": "ok", "items": loader.list_workflows()}

    @mcp.tool()
    @guard
    async def workflow_get(name_or_id: str):
        """按 template_id 或 site 取模板全文。"""
        from ..workflows import loader
        t, path = loader.get(name_or_id)
        layer = "package" if path.startswith(loader.PKG_DIR) else "user"
        return {"status": "ok", "template": t, "layer": layer}

    @mcp.tool()
    @guard
    async def workflow_publish(template_id: str, out_dir: str = None):
        """导出干净模板 JSON 供 PR（不联网）。"""
        from ..workflows import loader
        return {"status": "ok", **loader.publish(template_id, out_dir)}

    @mcp.tool()
    @guard
    async def workflow_stats():
        """所有模板的成功/失败统计。"""
        from ..workflows import loader
        return {"status": "ok", "stats": loader.stats()}

    @mcp.tool()
    @guard
    async def workflow_record_start(session_id: str = None,
                                    context_id: str = None,
                                    page_id: str = None):
        """开始录制：之后通过 locator_*/page_navigate 执行的动作会被记录。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        h = rt.pages.get(page_id) if page_id else None
        if h is None and rt.pages:
            try:
                h = rt.page_mgr.resolve()
            except Exception:
                h = None
        info = rt.recorder.start(
            session_id=session_id or (h.session_id if h else None),
            context_id=context_id or (h.context_id if h else None),
            page_id=page_id or (h.page_id if h else None))
        return {"status": "ok", **info}

    @mcp.tool()
    @guard
    async def workflow_record_stop(site: str = None, desc: str = "",
                                   name: str = None, template_id: str = None,
                                   parameterize: dict = None,
                                   sensitive: list = None):
        """停止录制，编译成模板草稿并保存。"""
        from ..runtime import get_runtime
        from ..workflows import compiler, loader
        rt = get_runtime()
        rec = rt.recorder.stop()
        if rec is None:
            raise GavelError("not_recording", "当前没有在录制")
        tid = template_id or "_".join(
            x for x in (loader.slugify(site) if site else None,
                        loader.slugify(name) if name else None) if x) \
            or "recorded_workflow"
        draft, errors, warnings = compiler.compile(
            rec.steps, template_id=tid, site=site or "", desc=desc,
            parameterize=parameterize, sensitive=sensitive)
        if errors:
            return {"status": "error", "reason": "compile_failed",
                    "errors": errors, "warnings": warnings,
                    "recorded_steps": len(rec.steps)}
        info = loader.save(draft)
        return {"status": "ok", **info, "warnings": warnings,
                "recorded_steps": len(rec.steps)}

    @mcp.tool()
    @guard
    async def workflow_run(name_or_id: str, params: dict = None,
                           session_id: str = None, verbosity: str = "summary",
                           run_id: str = None, confirm: bool = False,
                           wait_s: float = 6.0):
        """一次调用重放模板。

        verbosity: summary（默认，只回检查点摘要）| steps（每步）| full（含证据）。
        失败步自动升级为 full。requires_confirmation 步需 confirm=True 才执行。
        """
        from ..workflows import loader
        rt, runner = _runner()
        template, path = loader.get(name_or_id)
        result = await runner.run(
            template, params or {}, session_id=session_id, verbosity=verbosity,
            run_id=run_id, confirm=confirm, wait_s=wait_s)
        # 局部修复成功 → 发布新版本
        repaired = [s for s in result.get("steps", []) if s.get("repaired")]
        if repaired and not path.startswith(loader.PKG_DIR):
            for s in repaired:
                for st in template.get("steps", []):
                    if st.get("id") == s.get("step_id") and s.get("repaired_target"):
                        st.setdefault("action", {})["target"] = s["repaired_target"]
            info = loader.save(template)
            result["republished"] = info
        return result

    @mcp.tool()
    @guard
    async def workflow_replay(name_or_id: str, params: dict = None,
                              session_id: str = None, run_id: str = None,
                              confirm: bool = False):
        """同 workflow_run（summary 粒度）——语义化别名。"""
        return await workflow_run(name_or_id=name_or_id, params=params,
                                  session_id=session_id, verbosity="summary",
                                  run_id=run_id, confirm=confirm)

    @mcp.tool()
    @guard
    async def workflow_pause(run_id: str = None):
        """在步边界暂停一个运行中的 workflow。"""
        from ..runtime import get_runtime
        st = _find_run(get_runtime(), run_id)
        st.pause_event.clear()
        st.status = "paused"
        return {"status": "ok", "run_id": st.run_id, "paused": True}

    @mcp.tool()
    @guard
    async def workflow_resume(run_id: str = None):
        """恢复被暂停的 workflow（不重放已完成步骤）。"""
        from ..runtime import get_runtime
        st = _find_run(get_runtime(), run_id)
        st.pause_event.set()
        if st.status == "paused":
            st.status = "running"
        return {"status": "ok", "run_id": st.run_id, "paused": False}

    @mcp.tool()
    @guard
    async def workflow_cancel(run_id: str = None):
        """取消 workflow（在步边界生效）。"""
        from ..runtime import get_runtime
        st = _find_run(get_runtime(), run_id)
        st.cancelled = True
        st.pause_event.set()
        return {"status": "ok", "run_id": st.run_id, "cancelled": True}
