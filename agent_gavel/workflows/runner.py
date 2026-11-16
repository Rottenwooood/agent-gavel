"""工作流执行器（docs/tech-plan.md §5.15.3）。

- 单调用重放；verbosity=summary/steps/full，失败步自动升级
- 安全动作才按 failure_policy.retry 重试；副作用动作绝不自动重试
- requires_confirmation 步未确认则 blocked
- pause/resume/cancel（run 注册表 + Event）
- 局部修复：locator 失败时重新探索当前页，生成新 locator 重试
"""

import asyncio
import re
import time

from ..core.actions import spec as action_spec
from ..core.checks import from_checkpoint
from ..core.errors import GavelError
from . import schema as _schema


class RunState:
    def __init__(self, run_id, template_id):
        self.run_id = run_id
        self.template_id = template_id
        self.status = "running"
        self.pause_event = asyncio.Event()
        self.pause_event.set()  # set = 未暂停
        self.cancelled = False
        self.step_results = []
        self.result = None
        # 失败驻留所需：恢复时要能回到同一条链、同一个现场
        self.context_id = None
        self.steps = []
        self.ctx = None
        self.arts_before = set()
        self.verbosity = "summary"
        self.t0 = 0.0
        self.failed_index = None
        self.observed = {}


class Runner:
    def __init__(self, runtime, backend):
        self.runtime = runtime
        self.backend = backend

    async def run(
        self,
        template,
        params,
        *,
        context_id=None,
        verbosity="summary",
        run_id=None,
        confirm=False,
        wait_s=6.0,
        page_id=None,
    ):
        rt = self.runtime
        run_id = run_id or rt.next_id("run")
        t0 = time.perf_counter()

        page_ctx = None
        state = RunState(run_id, template.get("template_id"))
        rt.runs[run_id] = state
        try:
            page = await self._resolve_page(context_id, page_id)
            page_ctx = page.context
            await page_ctx.acquire_run(run_id)
            # preconditions：不满足立即阻断，绝不在错误页面上执行步骤
            ok, pc_detail = await _schema.check_preconditions(template, page.page)
            if not ok:
                failed_pc = [p for p in pc_detail if not p.get("ok")]
                res = {
                    "status": "blocked",
                    "run_id": run_id,
                    "template_id": template.get("template_id"),
                    "version": template.get("version"),
                    "reason": "precondition_failed",
                    "failed_preconditions": failed_pc,
                    "preconditions": pc_detail,
                    "steps_completed": 0,
                    "steps_total": len(template.get("steps", [])),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
                state.status = "blocked"
                state.result = res
                self._record_run(template.get("template_id"), "blocked")
                return res
            arts_before = {a["artifact_id"] for a in rt.artifacts.list()}
            steps = _schema.substitute(
                template.get("steps", []), params, template.get("parameters")
            )
            ctx = {
                "template": template,
                "page": page,
                "params": params,
                "wait_s": wait_s,
                "confirm": confirm,
                "page_map": {"page0": page.page_id},
                "new_count": 0,
            }
            state.context_id = page_ctx.context_id
            state.steps = steps
            state.ctx = ctx
            state.arts_before = arts_before
            state.verbosity = verbosity
            state.t0 = t0
            return await self._execute_steps(state, steps, ctx, arts_before, verbosity, t0)
        finally:
            if page_ctx is not None:
                page_ctx.release_run(run_id)
            # 失败必须驻留：runs 被 pop 掉，workflow_resume 就找不到现场了。
            # 只有"跑完/取消"才释放；失败留给 LLM 接手。
            if state.status not in ("fail", "ambiguous", "error"):
                rt.runs.pop(run_id, None)

    @staticmethod
    def _record_run(template_id, status):
        try:
            from . import loader

            loader.record_run(template_id, status)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程

    # ---- 会话/页面 ----
    async def _resolve_page(self, context_id=None, page_id=None):
        rt = self.runtime
        if page_id:
            return rt.page_mgr.get(page_id)
        if context_id:
            return rt.page_mgr.resolve(None, context_id=context_id)
        return rt.page_mgr.resolve()

    # ---- 主循环 ----
    async def _execute_steps(self, state, steps, ctx, arts_before, verbosity, t0, start_idx=0):
        rt = self.runtime
        template = ctx["template"]
        checkpoints_passed = 0
        failed = None
        cancelled = False

        for idx in range(start_idx, len(steps)):
            step = steps[idx]
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
                state.step_results.append(
                    {"step_id": step["id"], "status": "blocked", "reason": "requires_confirmation"}
                )
                failed = ("blocked", idx, "requires_confirmation")
                state.failed_index = idx
                break

            try:
                page = self._page_for(ctx, step)
            except GavelError as e:
                state.step_results.append(
                    {
                        "step_id": step["id"],
                        "status": "error",
                        "reason": e.code,
                        "error": e.message,
                    }
                )
                failed = ("error", idx, e.code)
                state.failed_index = idx
                break

            res = await self._run_one(page, step, ctx)
            self._bind_pages(ctx, res)
            state.step_results.append(res)
            if step.get("observe"):
                state.observed[step["id"]] = self._observed_payload(res)
            if res.get("checkpoint_passed"):
                checkpoints_passed += 1
            if res["status"] in ("fail", "ambiguous", "error"):
                # B1 安全重试一次（仅幂等/安全/非需确认步骤；瞬时/不确定失败）
                retried = await self._try_safe_retry(page, step, res, ctx)
                if retried is not None:
                    res = retried
                    state.step_results[-1] = retried
                    if retried.get("checkpoint_passed"):
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
                state.failed_index = idx
                break

        # 汇总
        arts_after = rt.artifacts.list()
        artifacts = [
            {
                "artifact_id": a["artifact_id"],
                "suggested_filename": a.get("suggested_filename"),
                "kind": a.get("kind"),
            }
            for a in arts_after
            if a["artifact_id"] not in arts_before
        ]
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
            "steps_completed": sum(1 for s in state.step_results if s["status"] in ("pass", "ok")),
            "steps_total": len(steps),
            "checkpoints_passed": checkpoints_passed,
            "artifacts": artifacts,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        if failed:
            fi = failed[1]
            result["failed_step"] = steps[fi].get("id") if fi < len(steps) else None
            result["reason"] = failed[2]
        if verbosity in ("steps", "full"):
            result["steps"] = state.step_results
        elif failed or cancelled:
            result["steps"] = [self._trim_step(s) for s in state.step_results]
        if state.observed:
            result["observed"] = state.observed
        repairs = [
            {
                "step_id": s.get("step_id"),
                "new_target": s.get("repaired_target"),
                "note": "运行时局部修复成功；改 JSON 里的 target 后 workflow_save 写回",
            }
            for s in state.step_results
            if s.get("repaired_target")
        ]
        if repairs:
            result["proposed_repairs"] = repairs
        if state.failed_index is not None and status in ("fail", "ambiguous", "error"):
            result["failed_step_index"] = state.failed_index
            result["resume_hint"] = "现场已保留：调 workflow_resume(run_id) 从失败步继续"
        state.result = result
        self._record_run(template.get("template_id"), status)
        return result

    # ---- 页面引用 ----
    def _page_for(self, ctx, step):
        ref = step.get("page") or "page0"
        pid = (ctx.get("page_map") or {}).get(ref)
        if pid is None:
            raise GavelError(
                "page_unbound",
                f"页面引用未绑定：{ref}",
                hint="该步依赖前面步骤打开的页面；检查录制时该页由哪一步产生",
            )
        return self.runtime.page_mgr.get(pid)

    @staticmethod
    def _bind_pages(ctx, res):
        """用本步 diff 里的 pages.opened 绑定 new:N —— 录制时写死的编号在
        重放时必然不同，只有 diff 能给出这次的对应关系。"""
        opened = ((res.get("diff") or {}).get("pages") or {}).get("opened") or []
        for item in opened:
            pid = item.get("page_id")
            if not pid:
                continue
            ref = f"new:{ctx['new_count']}"
            ctx["new_count"] += 1
            ctx["page_map"].setdefault(ref, pid)

    # ---- 静默执行：默认不吐中间结果 ----
    _TRIM_KEYS = (
        "step_id",
        "status",
        "reason",
        "error",
        "assertions",
        "diff",
        "checkpoint_passed",
        "repaired",
        "retried",
    )

    @classmethod
    def _trim_step(cls, res):
        return {k: v for k, v in res.items() if k in cls._TRIM_KEYS and v is not None}

    @classmethod
    def _observed_payload(cls, res):
        keep = ("step_id", "action_type", "status", "execution", "assertions", "diff")
        return {k: v for k, v in res.items() if k in keep and v is not None}

    # ---- 失败续跑：从失败步继续，不重放已完成步骤 ----
    async def resume(self, run_id):
        rt = self.runtime
        state = rt.runs.get(run_id)
        if state is None:
            raise GavelError(
                "run_not_found",
                f"未知 run：{run_id}",
                hint="失败现场可能已被释放（只有 fail/ambiguous/error 会保留）",
            )
        if state.status not in ("fail", "ambiguous", "error"):
            raise GavelError("not_resumable", f"该 run 当前状态不可续跑：{state.status}")
        if state.ctx is None:
            raise GavelError("not_resumable", "缺少运行上下文，无法续跑")
        ctx = state.ctx
        # 重新读取模板：失败驻留期间作者可能已经 patch 过（放宽断言/换锚点），
        # 续跑必须用最新版本，否则改完还是按旧链跑。
        try:
            from . import loader
            from . import schema as _schema

            template, _p = loader.get_exact(state.template_id)
            ctx["template"] = template
            state.steps = _schema.substitute(
                template.get("steps", []), ctx.get("params") or {}, template.get("parameters")
            )
        except Exception:
            pass  # silent-ok: 模板不可读时退回内存中的链
        page = rt.page_mgr.get(ctx["page_map"]["page0"])
        page_ctx = page.context
        await page_ctx.acquire_run(run_id)
        start = state.failed_index if state.failed_index is not None else 0
        state.step_results = state.step_results[:start]
        state.failed_index = None
        state.status = "running"
        try:
            return await self._execute_steps(
                state,
                state.steps,
                ctx,
                state.arts_before,
                state.verbosity,
                time.time(),
                start_idx=start,
            )
        finally:
            page_ctx.release_run(run_id)
            if state.status not in ("fail", "ambiguous", "error"):
                rt.runs.pop(run_id, None)

    # ---- 单步 ----
    async def _run_one(self, page, step, ctx):
        be = self.backend
        act = step["action"]
        atype = act["type"]
        wait_s = ctx.get("wait_s", 6.0)
        checks = from_checkpoint(step.get("checkpoint"))
        started = time.perf_counter()
        entry = {"step_id": step["id"], "action_type": atype}
        try:
            exec_info = await self._dispatch(page, act)
            entry["status"] = "ok"
            entry["execution"] = exec_info
            diff = exec_info.get("diff") if isinstance(exec_info, dict) else None
            if diff:
                entry["diff"] = diff
            if atype == "expect":
                # expect 的成败就是步骤成败（此前被统一置为 ok，导致失败仍整体 pass）
                entry["status"] = exec_info.get("status", "ok")
                if exec_info.get("assertions") is not None:
                    entry["assertions"] = exec_info["assertions"]
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
        if checks and atype != "expect":
            ev = await be.evaluate_checks(
                page, checks, obs=entry.get("diff"), wait_s=wait_s, target=act.get("target")
            )
            entry["status"] = ev["status"]
            entry["assertions"] = ev["assertions"]
            entry["checkpoint_passed"] = ev["status"] == "pass"
            if ev.get("diff") is not None:
                entry["diff"] = ev["diff"]
        elif atype == "expect":
            entry["checkpoint_passed"] = entry["status"] == "pass"
        return entry

    _HANDLERS = {
        "navigate": "_do_navigate",
        "wait_for_selector": "_do_wait_selector",
        "wait_for_url": "_do_wait_url",
        "wait_for_text": "_do_wait_text",
        "expect": "_do_expect",
        "screenshot": "_do_screenshot",
        "pdf": "_do_pdf",
    }

    async def _dispatch(self, page, act):
        name = self._HANDLERS.get(act["type"])
        if name is not None:
            return await getattr(self, name)(page, act)
        if action_spec(act["type"]).target_required:
            return await self._do_target(page, act)
        raise GavelError("bad_action", f"未知动作：{act['type']}")

    async def _do_navigate(self, page, act):
        be, rt = self.backend, self.runtime
        rt.policies.check_domain(act["url"])
        return await page.actor.submit(
            "navigate",
            lambda: be.goto(
                page,
                act["url"],
                wait_until=act.get("wait_until", "domcontentloaded"),
                timeout_s=act.get("timeout_s", 30),
            ),
        )

    async def _do_wait_selector(self, page, act):
        be = self.backend
        return await page.actor.submit(
            "wait_for_selector",
            lambda: be.wait_for_selector(
                page,
                act["selector"],
                state=act.get("state", "visible"),
                timeout_s=act.get("timeout_s", 30),
            ),
        )

    async def _do_wait_url(self, page, act):
        be = self.backend
        return await page.actor.submit(
            "wait_for_url",
            lambda: be.wait_for_url(page, act["url"], timeout_s=act.get("timeout_s", 30)),
        )

    async def _do_wait_text(self, page, act):
        be = self.backend
        return await page.actor.submit(
            "wait_for_text",
            lambda: be.wait_for_text(
                page, act["text"], timeout_s=act.get("timeout_s", 30), selector=act.get("selector")
            ),
        )

    async def _do_expect(self, page, act):
        ev = await self.backend.evaluate_checks(
            page, act.get("checks") or act, wait_s=act.get("wait_s", 6.0)
        )
        return {
            "action": "expect",
            "status": ev["status"],
            "assertions": ev.get("assertions"),
            "diff": ev.get("diff"),
        }

    async def _do_screenshot(self, page, act):
        be, rt = self.backend, self.runtime
        res = await page.actor.submit(
            "screenshot", lambda: be.screenshot(page, full_page=bool(act.get("full_page")))
        )
        info = await asyncio.to_thread(
            rt.artifacts.save_bytes,
            res["data"],
            filename=f"{page.page_id}.png",
            kind="screenshot",
            mime_type="image/png",
            page_id=page.page_id,
            context_id=page.context_id,
        )
        return {"action": "screenshot", "artifact_id": info["artifact_id"]}

    async def _do_pdf(self, page, act):
        be, rt = self.backend, self.runtime
        res = await page.actor.submit("pdf", lambda: be.pdf(page))
        info = await asyncio.to_thread(
            rt.artifacts.save_bytes,
            res["data"],
            filename=f"{page.page_id}.pdf",
            kind="pdf",
            mime_type="application/pdf",
            page_id=page.page_id,
            context_id=page.context_id,
        )
        return {"action": "pdf", "artifact_id": info["artifact_id"]}

    async def _do_target(self, page, act):
        be = self.backend
        atype = act["type"]
        kw = {
            k: act[k]
            for k in (
                "button",
                "count",
                "force",
                "value",
                "text",
                "keys",
                "select_by",
                "files",
                "destination",
                "delay_ms",
            )
            if k in act
        }
        if atype == "type" and "value" not in kw and act.get("text") is not None:
            kw["value"] = act["text"]
        return await page.actor.submit(
            atype,
            lambda: be.act(
                page, act.get("target"), atype, timeout_s=act.get("timeout_s", 30), **kw
            ),
        )

    @staticmethod
    def _retry_allowed(step, res):
        """该步骤失败后是否允许自动重试/修定位。

        - 需确认的步骤：不允许。
        - 显式"可重试且幂等"：允许。
        - 其余（如 click/press 默认非幂等）：仅当**动作根本未执行**（定位失败
          locator_not_found）时允许——没点到就不会有副作用；若显式声明过
          safe_to_retry/idempotent（哪怕是 False），尊重声明，不给这个例外。
        """
        if step.get("requires_confirmation"):
            return False
        if step.get("safe_to_retry") and step.get("idempotent"):
            return True
        return res.get("reason") == "locator_not_found" and not step.get("retry_explicit")

    # ---- B1 安全重试 ----
    async def _try_safe_retry(self, page, step, res, ctx):
        """安全且幂等的步骤，瞬时/不确定失败时原样重试一次。

        只重试通过 `_retry_allowed` 判定的步骤；副作用动作绝不重试。
        策略由模板的 failure_policy.retry 控制：none 禁用，safe_only（默认）。
        """
        act = step["action"]
        if action_spec(act["type"]).retry_scope != "target":
            return None
        if not self._retry_allowed(step, res):
            return None
        policy = (ctx.get("template", {}).get("failure_policy") or {}).get("retry")
        if policy == "none":
            return None
        out = await self._run_one(page, step, ctx)
        if out.get("status") in ("pass", "ok"):
            out["retried"] = True
            out["retry_of"] = res.get("status")
            return out
        out["retried"] = True
        out["retry_failed"] = True
        out["retry_of"] = res.get("status")
        return out

    @staticmethod
    def _repair_needles(target, step):
        """从目标里抽可匹配的线索：语义名（name/text/label）、目标值原文、
        css/xpath 值里的标识 token。用于换锚点时在原页面里找回同一元素。"""
        needles = []
        for k in ("name", "text", "label"):
            v = target.get(k)
            if v:
                needles.append(str(v))
        val = target.get("value")
        if val:
            needles.append(str(val))  # 原文（css "#search" 可命中 "#search-btn"）
            for tok in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{3,}", str(val)):
                needles.append(tok)
        out = []
        for n in needles:
            n = str(n).strip()
            if n and n.lower() not in [x.lower() for x in out]:
                out.append(n)
        return out

    @staticmethod
    def _repair_score(el, needles):
        """候选元素与线索的匹配分：文本全等 > 文本包含 > id/name/aria > 锚点 >
        placeholder。避免"输入框 placeholder 含关键词"抢过真正的按钮。"""
        text = (el.get("text") or "").strip().lower()
        ident = " ".join(str(el.get(f) or "") for f in ("id", "name", "aria", "role")).lower()
        ph = (el.get("placeholder") or "").lower()
        anc = str((el.get("anchor") or {}).get("value", "")).lower()
        score = 0
        for n in needles:
            nl = n.lower()
            if not nl:
                continue
            if text == nl:
                score += 100
            elif nl in text:
                score += 40
            if nl in ident:
                score += 30
            if nl in anc:
                score += 25
            if nl in ph:
                score += 5
        return score

    # ---- 局部修复 ----
    async def _try_repair(self, page, step, res, template):
        policy = (template.get("failure_policy") or {}).get("repair")
        if policy != "explore_local_step":
            return None
        act = step["action"]
        if action_spec(act["type"]).retry_scope != "target":
            return None
        # 副作用安全：需确认 / 不可重试 / 非幂等的步骤，失败后绝不重新点击
        if not self._retry_allowed(step, res):
            return None
        if not (res.get("repairable") or res.get("status") in ("fail", "ambiguous")):
            return None
        # 非幂等动作（如 click）修复会真的再点一次——必须有检查点兜底，
        # 否则可能点到错误元素产生副作用，宁可放弃修复。
        if not step.get("idempotent") and not step.get("checkpoint"):
            return None
        be = self.backend
        target = act.get("target") or {}
        needles = self._repair_needles(target, step)
        if not needles:
            return None
        try:
            found = await be.explore(page)
        except Exception:
            return None
        match, best = None, 0
        for el in found.get("items", []):
            if not el.get("anchor"):
                continue
            score = self._repair_score(el, needles)
            if score > best:
                match, best = el, score
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
