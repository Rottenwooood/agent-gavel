"""结构化错误：所有对外失败归一为 GavelError。

code 供机器判断，message 给人看，detail 带上下文，hint 说明下一步，
retryable 表示技术上是否可重试。未知异常统一落到 internal，并附带
trace_id 与截断堆栈，便于定位而不是只看到一句 "TypeError: ..."。
"""

import logging
import traceback
import uuid

STATUS_VALUES = frozenset({"ok", "pass", "fail", "ambiguous", "error", "blocked", "cancelled"})

_log = logging.getLogger("agent_gavel")


class GavelError(Exception):
    """agent-gavel 的统一错误类型。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail=None,
        hint=None,
        retryable: bool = False,
        trace_id: str = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.hint = hint
        self.retryable = retryable
        self.trace_id = trace_id

    def to_dict(self):
        out = {
            "status": "error",
            "reason": self.code,
            "error": self.message,
            "retryable": self.retryable,
        }
        if self.trace_id:
            out["trace_id"] = self.trace_id
        if self.detail is not None:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def __repr__(self):  # pragma: no cover - debug only
        return f"GavelError({self.code!r}, {self.message!r})"


def wrap_exception(e: Exception) -> GavelError:
    """把任意异常转成 GavelError（已是的原样返回），并记录可定位的 trace_id。"""
    if isinstance(e, GavelError):
        return e
    trace_id = uuid.uuid4().hex[:12]
    stack = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    _log.error("internal error [%s]\n%s", trace_id, stack[-4000:])
    return GavelError(
        "internal",
        f"{type(e).__name__}: {e}",
        trace_id=trace_id,
        hint="带 trace_id 反馈；服务端日志里有完整堆栈",
    )
