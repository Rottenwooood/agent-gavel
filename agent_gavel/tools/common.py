"""工具层公共：guard 装饰器、runtime/backend 解析、id 拼装。"""

import functools
import time
import uuid

from ..backends.playwright import PlaywrightBackend
from ..core.errors import STATUS_VALUES, GavelError, wrap_exception
from ..runtime import get_runtime

_BACKEND = None


def backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = PlaywrightBackend(get_runtime())
    return _BACKEND


def envelope(result, *, operation_id=None, elapsed_ms=None):
    """统一响应外形：status 取值归一，补 operation_id / elapsed_ms。"""
    if not isinstance(result, dict):
        return result
    status = result.get("status")
    if status == "no_match":
        result["status"] = "fail"
        result.setdefault("reason", "selector_not_found")
    elif status == "partial_error":
        result["status"] = "ok"
        result["complete"] = False
    elif status not in STATUS_VALUES:
        result["reason"] = result.get("reason") or status
        result["status"] = "error"
    if operation_id:
        result.setdefault("operation_id", operation_id)
    if elapsed_ms is not None:
        result.setdefault("elapsed_ms", elapsed_ms)
    return result


def guard(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        operation_id = "op_" + uuid.uuid4().hex[:8]
        started = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
        except GavelError as e:
            result = e.to_dict()
        except Exception as e:  # noqa: BLE001
            result = wrap_exception(e).to_dict()
        return envelope(
            result,
            operation_id=operation_id,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    return wrapper


def ids(handle):
    out = {"context_id": handle.context_id, "page_id": handle.page_id}
    lease = handle.context.lease_session_id()
    if lease:
        out["session_id"] = lease
    return out


def rec(action_type, target=None, **kw):
    """构造录制用的动作描述。"""
    r = {"type": action_type}
    if target:
        r["target"] = target
    for k, v in kw.items():
        if v is not None:
            r[k] = v
    return r


async def run_with_optional_assert(
    rt, handle, action_type, fn, *, assertions=None, wait_s=6.0, timeout_s=30.0, record=None
):
    """执行动作，若给断言则做三态判定（含 before 签名 diff 判 ambiguous）。"""
    be = backend()
    target = (record or {}).get("target")
    before = await be.page_signature(handle, target=target) if assertions else None
    url_before = handle.page.url
    execution = await handle.actor.submit(action_type, fn, timeout_s=timeout_s + 5)
    out = {"status": "ok", **ids(handle), "execution": execution}
    passed = None
    if assertions:
        ev = await be.evaluate_checks(
            handle, assertions, before_sig=before, wait_s=wait_s, target=target
        )
        out["status"] = ev["status"]
        out["assertions"] = ev["assertions"]
        passed = ev["status"] == "pass"
        if ev.get("diff") is not None:
            out["diff"] = ev["diff"]
    if rt.recorder is not None and rt.recorder.active and record is not None:
        rt.recorder.record(
            handle,
            record,
            url_before=url_before,
            url_after=handle.page.url,
            checkpoint=assertions if passed else None,
        )
    return out


ATTACH_WARNING = (
    "attach 模式：正在接管一个已存在的浏览器。注意——你控制的是用户真实账号环境，"
    "本机任何进程都能通过该调试端口操作它；本项目不会关闭它；运行期间用户关闭浏览器"
    "会导致会话中断；同一资料目录存在单实例限制。仅在你明确需要时使用。"
)


async def resolve_session(session_id=None):
    rt = get_runtime()
    if session_id:
        return rt.session_mgr.get(session_id)
    if len(rt.sessions) == 1:
        return next(iter(rt.sessions.values()))
    if not rt.sessions:
        raise GavelError("session_required", "还没有 session", hint="先 session_create")
    raise GavelError(
        "session_ambiguous",
        "有多个 session，请显式指定 session_id",
        detail={"sessions": list(rt.sessions)},
    )


async def resolve_context(context_id=None, session_id=None):
    rt = get_runtime()
    if context_id:
        return rt.context_mgr.get(context_id)
    session = await resolve_session(session_id)
    if session.context_ids:
        return rt.context_mgr.get(session.context_ids[0])
    raise GavelError("context_not_found", "该 session 下没有 context", hint="先 context_create")
