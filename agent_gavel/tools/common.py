"""工具层公共：guard 装饰器、runtime/backend 解析、id 拼装。"""

import functools

from ..backends.playwright_backend import PlaywrightBackend
from ..runtime import get_runtime
from ..runtime.errors import GavelError, wrap_exception

_BACKEND = None


def backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = PlaywrightBackend(get_runtime())
    return _BACKEND


def guard(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except GavelError as e:
            return e.to_dict()
        except Exception as e:  # noqa: BLE001
            return wrap_exception(e).to_dict()
    return wrapper


def ids(handle):
    return {"session_id": handle.session_id, "context_id": handle.context_id,
            "page_id": handle.page_id}


def rec(action_type, target=None, **kw):
    """构造录制用的动作描述。"""
    r = {"type": action_type}
    if target:
        r["target"] = target
    for k, v in kw.items():
        if v is not None:
            r[k] = v
    return r


async def run_with_optional_assert(rt, handle, action_type, fn, *,
                                   assertions=None, wait_s=6.0, timeout_s=30.0,
                                   record=None):
    """执行动作，若给断言则做三态判定（含 before 签名 diff 判 ambiguous）。"""
    be = backend()
    before = await be.page_signature(handle.page) if assertions else None
    url_before = handle.page.url
    execution = await handle.actor.submit(action_type, fn,
                                          timeout_s=timeout_s + 5)
    out = {"status": "ok", **ids(handle), "execution": execution}
    if rt.recorder is not None and rt.recorder.active and record is not None:
        rt.recorder.record(handle, record, url_before=url_before,
                           url_after=handle.page.url)
    if assertions:
        ev = await be.evaluate_checks(handle.page, assertions,
                                      before_sig=before, wait_s=wait_s)
        out["status"] = ev["status"]
        out["assertions"] = ev["assertions"]
    return out
