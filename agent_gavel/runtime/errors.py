"""结构化错误：所有对外失败归一为 GavelError，绝不让 MCP 层包成通用异常。

设计见 docs/tech-plan.md §5.1。每个错误带 code（机器可判）、message（人看）、
detail（可选上下文）、hint（下一步怎么办）、retryable（技术上可否重试）。
"""


class GavelError(Exception):
    """agent-gavel 的统一错误类型。"""

    def __init__(self, code: str, message: str, *, detail=None, hint=None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.hint = hint
        self.retryable = retryable

    def to_dict(self):
        out = {
            "status": "error",
            "reason": self.code,
            "error": self.message,
            "retryable": self.retryable,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def __repr__(self):  # pragma: no cover - debug only
        return f"GavelError({self.code!r}, {self.message!r})"


def wrap_exception(e: Exception) -> GavelError:
    """把任意异常转成 GavelError（已是的原样返回）。"""
    if isinstance(e, GavelError):
        return e
    return GavelError("internal", f"{type(e).__name__}: {e}",
                      hint="若反复出现，请带此消息反馈")
