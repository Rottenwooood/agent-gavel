"""工作流执行器（docs/tech-plan.md §5.15.3）。

- 单调用重放；verbosity=summary/steps/full，失败步自动升级
- 安全动作才按 failure_policy.retry 重试；副作用动作绝不自动重试
- requires_confirmation 步未确认则 blocked
- pause/resume/cancel（run 注册表 + Event）
- 局部修复：locator 失败时重新探索当前页，生成新 locator 重试
"""

import asyncio
import time

from ..runtime.errors import GavelError
from . import schema as _schema

_TARGETLESS = {"navigate", "wait_for_selector", "wait_for_url", "expect",
               "screenshot", "pdf"}


class RunState:
    def __init__(self, run_id, template_id):
        self.run_id = run_id
        self.template_id = template_id
        self.status = "running"
        self.pause_event = asyncio.Event()
        self.pause_event.set()          # set = 未暂停
        self.cancelled = False
        self.step_results = []
        self.result = None


class Runner:
    def __init__(self, runtime, backend):
        self.runtime = runtime
        self.backend = backend

    async def run(self, template, params, *, session_id=None,
                  verbosity="summary", run_id=None, confirm=False,
                  wait_s=6.0):
        rt = self.runtime
        be = self.backend
        run_id = run_id or rt.next_id("run")
        t0 = time.perf_counter()

        session, owned = await self._resolve_session(session_id)
        await session.acquire(run_id)
        state = RunState(run_id, template.get("template_id"))
        rt.runs[run_id] = state
        try:
            page = await self._resolve_page(session)
            # preconditions
            ok, pc_detail = await _schema.check_preconditions(template, page.page)
            arts_before = {a["artifact_id"] for a in rt.artifacts.list()}
            steps = _schema.substitute(template.get("steps", []), params)
            ctx = {"template": template, "page": page, "params": params,
                   "wait_s": wait_s, "confirm": confirm}
            return await self._execute_steps(state, steps, ctx, arts_before,
                                             verbosity, t0)
        finally:
            session.release(run_id)
            rt.runs.pop(run_id, None)
            if owned:
                try:
                    await rt.session_mgr.close(session.session_id)
                except Exception:
                    pass

    # ---- 会话/页面 ----
    async def _resolve_session(self, session_id):
        rt = self.runtime
        if session_id:
            return rt.session_mgr.get(session_id), False
        if len(rt.sessions) == 1:
            return next(iter(rt.sessions.values())), False
        if not rt.sessions:
            s = await rt.session_mgr.create(mode="ephemeral")
            return s, True
        raise GavelError("session_ambiguous", "有多个 session，请指定 session_id",
                         detail={"sessions": list(rt.sessions)})

    async def _resolve_page(self, session):
        rt = self.runtime
        if not session.context_ids:
            ctx = await rt.context_mgr.create(session, {})
        else:
            ctx = rt.contexts[session.context_ids[0]]
        return rt.page_mgr.resolve(ctx.active_page_id)

    # ---- 主循环 ----
    async def _execute_steps(self, state, steps, ctx, arts_before, verbosity,
                             t0):
        rt, be, page = self.runtime, self.backend, ctx["page"]
        template = ctx["template"]
        checkpoints_passed = 0
        failed = None
        cancelled = False

        for idx, step in enumerate(steps):
            # 取消 / 暂停（步边界）
            if state.cancelled:
                cancelled = True
                break
            if not state.pause_event.is_set():
                state.status = "paused"
                await state.pause_event.wait()
                if state.cancelled:
                    cancelled = True
                    break
                state.status = "running"

            if step.get("requires_confirmation") and not ctx["confirm"]:
                state.step_results.append({
                    "step_id": step["id"], "status": "blocked",
                    "reason": "requires_confirmation"})
                failed = ("blocked", idx, "requires_confirmation")
                break

            res = await self._run_one(page, step, ctx)
            state.step_results.append(res)
            if res.get("checkpoint_passed"):
                checkpoints_passed += 1
            if res["status"] in ("fail", "ambiguous", "error"):
                # 局部修复
                repaired = await self._try_repair(page, step, res, template)
                if repaired is not None:
                    state.step_results[-1] = repaired
                    if repaired.get("checkpoint_passed"):
                        checkpoints_passed += 1
                    continue
                failed = (res["status"], idx, res.get("reason"))
                break

        # 汇总
        arts_after = rt.artifacts.list()
        artifacts = [{"artifact_id": a["artifact_id"],
                      "suggested_filename": a.get("suggested_filename"),
                      "kind": a.get("kind")}
                     for a in arts_after if a["artifact_id"] not in arts_before]
        if cancelled:
            status = "cancelled"
        elif failed:
            status = failed[0]
        else:
            status = "pass"
        state.status = status

        result = {
            "status": status,
            "run_id": state.run_id,
            "template_id": template.get("template_id"),
            "version": template.get("version"),
            "steps_completed": sum(1 for s in state.step_results
                                   if s["status"] in ("pass", "ok")),
            "steps_total": len(steps),
            "checkpoints_passed": checkpoints_passed,
            "artifacts": artifacts,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        if failed:
            fi = failed[1]
            result["failed_step"] = steps[fi].get("id") if fi < len(steps) else None
            result["reason"] = failed[2]
        if verbosity in ("steps", "full") or failed or cancelled:
            result["steps"] = state.step_results
        if verbosity == "full":
            result["steps"] = state.step_results
        state.result = result
        try:
            from . import loader
            loader.record_run(template.get("template_id"), status)
        except Exception:
            pass
        return result

    # ---- 单步 ----
    async def _run_one(self, page, step, ctx):
        be = self.backend
        act = step["action"]
        atype = act["type"]
        wait_s = ctx.get("wait_s", 6.0)
        checks = _schema.checkpoint_to_checks(step.get("checkpoint"))
        if isinstance(checks, dict) and "checkpoint" in checks:
            checks = checks["checkpoint"]
        before = await be.page_signature(page.page) if checks else None
        started = time.perf_counter()
        entry = {"step_id": step["id"], "action_type": atype}
        try:
            exec_info = await self._dispatch(page, act)
            entry["status"] = "ok"
            entry["execution"] = exec_info
        except GavelError as e:
            entry["status"] = "error"
            entry["reason"] = e.code
            entry["error"] = e.message
            if e.code == "locator_not_found":
                entry["repairable"] = True
            return entry
        except Exception as e:  # noqa: BLE001
            entry["status"] = "error"
            entry["reason"] = "exception"
            entry["error"] = str(e)
            return entry
        entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if checks:
            ev = await be.evaluate_checks(page.page, checks,
                                          before_sig=before, wait_s=wait_s)
            entry["status"] = ev["status"]
            entry["assertions"] = ev["assertions"]
            entry["checkpoint_passed"] = ev["status"] == "pass"
        return entry

    async def _dispatch(self, page, act):
        be = self.backend
        rt = self.runtime
        atype = act["type"]
        if atype == "navigate":
            rt.policies.check_domain(act["url"])
            return await page.actor.submit(
                "navigate", lambda: be.goto(page, act["url"],
                                            wait_until=act.get("wait_until",
                                                               "domcontentloaded"),
                                            timeout_s=act.get("timeout_s", 30)))
        if atype == "wait_for_selector":
            return await page.actor.submit(
                "wait_for_selector",
                lambda: be.wait_for_selector(page, act["selector"],
                                             state=act.get("state", "visible"),
                                             timeout_s=act.get("timeout_s", 30)))
        if atype == "wait_for_url":
            return await page.actor.submit(
                "wait_for_url", lambda: be.wait_for_url(
                    page, act["url"], timeout_s=act.get("timeout_s", 30)))
        if atype == "expect":
            ev = await be.evaluate_checks(page.page, act.get("checks") or act,
                                          wait_s=act.get("wait_s", 6.0))
            return {"action": "expect", "status": ev["status"]}
        if atype == "screenshot":
            res = await page.actor.submit(
                "screenshot", lambda: be.screenshot(page,
                                                    full_page=bool(act.get("full_page"))))
            info = rt.artifacts.save_bytes(res["data"], filename=f"{page.page_id}.png",
                                           kind="screenshot", mime_type="image/png",
                                           page_id=page.page_id,
                                           session_id=page.session_id)
            return {"action": "screenshot", "artifact_id": info["artifact_id"]}
        if atype == "pdf":
            res = await page.actor.submit("pdf", lambda: be.pdf(page))
            info = rt.artifacts.save_bytes(res["data"], filename=f"{page.page_id}.pdf",
                                           kind="pdf", mime_type="application/pdf",
                                           page_id=page.page_id,
                                           session_id=page.session_id)
            return {"action": "pdf", "artifact_id": info["artifact_id"]}
        # 目标型动作
        kw = {}
        for k in ("button", "count", "value", "text", "keys", "select_by",
                  "files", "destination", "full_page"):
            if k in act:
                kw[k] = act[k]
        return await page.actor.submit(
            atype, lambda: be.act(page.page, act.get("target"), atype,
                                  timeout_s=act.get("timeout_s", 30), **kw))

    # ---- 局部修复 ----
    async def _try_repair(self, page, step, res, template):
        policy = (template.get("failure_policy") or {}).get("repair")
        if policy != "explore_local_step":
            return None
        act = step["action"]
        if act.get("type") not in _TARGET_ACTIONS_SAFE:
            return None
        if not (res.get("repairable") or res.get("status") in ("fail", "ambiguous")):
            return None
        be = self.backend
        name = None
        target = act.get("target") or {}
        name = target.get("value") or target.get("name") or step.get("id")
        try:
            found = await be.explore(page.page)
        except Exception:
            return None
        match = None
        for el in found.get("items", []):
            hay = f"{el.get('text','')} {el.get('aria','')} {el.get('placeholder','')}"
            if name and str(name) in hay and el.get("anchor"):
                match = el
                break
        if not match:
            return None
        # 用新锚点重试一次（anchor{k} → target{by:...}）
        anc = dict(match["anchor"])
        new_target = {"by": anc.get("kind", "css"), "value": anc.get("value")}
        if anc.get("name"):
            new_target["name"] = anc["name"]
        new_act = dict(act)
        new_act["target"] = new_target
        retry_step = {"id": step["id"], "action": new_act}
        if step.get("checkpoint"):
            retry_step["checkpoint"] = step["checkpoint"]
        out = await self._run_one(page, retry_step, {"wait_s": 6.0})
        if out.get("status") in ("pass", "ok"):
            out["repaired"] = True
            out["repaired_target"] = new_target
            return out
        return None


_TARGET_ACTIONS_SAFE = {"click", "fill", "type", "press", "check", "uncheck",
                        "select", "hover", "focus", "clear"}
