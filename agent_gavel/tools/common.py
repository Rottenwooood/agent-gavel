"""工具层公共：runtime/backend 解析、id 拼装。"""

from ..backends.playwright import PlaywrightBackend
from ..core.errors import STATUS_VALUES
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


def ids(handle):
    return {"context_id": handle.context_id, "page_id": handle.page_id}


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
    rt,
    handle,
    action_type,
    fn,
    *,
    assertions=None,
    wait_s=6.0,
    timeout_s=30.0,
    record=None,
    flatten=False,
    post=None,
):
    """执行动作，若给断言则做三态判定（diff 证据来自动作自身的 Observation）。"""
    be = backend()
    target = (record or {}).get("target")
    url_before = handle.page.url
    execution = await handle.actor.submit(action_type, fn, timeout_s=timeout_s + 5)
    diff = execution.pop("diff", None) if isinstance(execution, dict) else None
    if flatten:
        out = {"status": "ok", **ids(handle), **execution}
    else:
        out = {"status": "ok", **ids(handle), "execution": execution}
    if post is not None:
        await post(out, execution, handle)
    if diff:
        out["diff"] = diff
    passed = None
    if assertions:
        ev = await be.evaluate_checks(handle, assertions, obs=diff, wait_s=wait_s, target=target)
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
            diff=diff,
        )
    return out


ATTACH_WARNING = (
    "attach 模式：正在接管一个已存在的浏览器。注意——你控制的是用户真实账号环境，"
    "本机任何进程都能通过该调试端口操作它；本项目不会关闭它；运行期间用户关闭浏览器"
    "会导致会话中断；同一资料目录存在单实例限制。仅在你明确需要时使用。"
)


async def resolve_context(context_id=None, profile=None, isolated=None):
    """取 context：显式 id > 已有默认容器 > 按 profile 新建/复用。"""
    rt = get_runtime()
    if context_id:
        return rt.context_mgr.get(context_id)
    return await rt.context_mgr.ensure(profile=profile, isolated=bool(isolated))
