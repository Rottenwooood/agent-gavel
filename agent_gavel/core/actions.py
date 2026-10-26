"""动作词表：类型、必填字段、幂等性、副作用判定、录制字段。

这是动作语义的唯一来源。schema 校验、runner 调度、录制字段都从这里读，
不允许在别处再维护一份动作清单。
"""

from dataclasses import dataclass

from .errors import GavelError


@dataclass(frozen=True)
class ActionSpec:
    type: str
    target_required: bool = False
    required_fields: tuple = ()
    idempotent: bool = True
    retry_scope: str = "none"  # target | none
    wait_kind: bool = False
    record_fields: tuple = ()


# 说明：
# - click/press/type/drag 默认非幂等：可能提交、追加或触发导航。
# - fill 写入的是最终值，重复执行结果一致；但部分站点会因 input 事件即时提交，
#   因此副作用判定仍需结合目标文案（looks_like_side_effect）。
# - upload 重复设置同一组文件结果一致，视为幂等。
ACTIONS = {
    "navigate": ActionSpec("navigate", required_fields=("url",), record_fields=("url",)),
    "click": ActionSpec(
        "click",
        target_required=True,
        idempotent=False,
        retry_scope="target",
        record_fields=("button",),
    ),
    "fill": ActionSpec(
        "fill",
        target_required=True,
        required_fields=("value",),
        retry_scope="target",
        record_fields=("value",),
    ),
    "type": ActionSpec(
        "type",
        target_required=True,
        required_fields=("value",),
        idempotent=False,
        retry_scope="target",
        record_fields=("text",),
    ),
    "press": ActionSpec(
        "press",
        target_required=True,
        idempotent=False,
        retry_scope="target",
        record_fields=("keys",),
    ),
    "check": ActionSpec("check", target_required=True, retry_scope="target"),
    "uncheck": ActionSpec("uncheck", target_required=True, retry_scope="target"),
    "select": ActionSpec(
        "select", target_required=True, retry_scope="target", record_fields=("value", "select_by")
    ),
    "hover": ActionSpec("hover", target_required=True, retry_scope="target"),
    "focus": ActionSpec("focus", target_required=True, retry_scope="target"),
    "clear": ActionSpec("clear", target_required=True, retry_scope="target"),
    "scroll": ActionSpec(
        "scroll", target_required=True, retry_scope="target", record_fields=("direction", "amount")
    ),
    "upload": ActionSpec(
        "upload", target_required=True, retry_scope="target", record_fields=("files",)
    ),
    "drag": ActionSpec(
        "drag",
        target_required=True,
        idempotent=False,
        retry_scope="target",
        record_fields=("destination",),
    ),
    "wait_for_selector": ActionSpec(
        "wait_for_selector",
        required_fields=("selector",),
        wait_kind=True,
        record_fields=("selector", "state", "timeout_s"),
    ),
    "wait_for_url": ActionSpec(
        "wait_for_url", required_fields=("url",), wait_kind=True, record_fields=("url", "timeout_s")
    ),
    "wait_for_text": ActionSpec(
        "wait_for_text",
        required_fields=("text",),
        wait_kind=True,
        record_fields=("text", "selector", "timeout_s"),
    ),
    "expect": ActionSpec("expect", wait_kind=True, record_fields=("checks", "wait_s")),
    "screenshot": ActionSpec("screenshot", record_fields=("full_page",)),
    "pdf": ActionSpec("pdf"),
}

# 命中这些词的点击/输入需要确认，且默认不自动重试。
_SIDE_EFFECT_HINTS = (
    "提交",
    "保存",
    "删除",
    "支付",
    "付款",
    "购买",
    "下单",
    "确认",
    "submit",
    "save",
    "delete",
    "pay",
    "buy",
    "purchase",
    "confirm",
    "send",
)


def spec(action_type: str) -> ActionSpec:
    try:
        return ACTIONS[action_type]
    except KeyError:
        raise GavelError("bad_action", f"未知动作：{action_type}") from None


def looks_like_side_effect(action: dict) -> bool:
    """目标文案命中副作用词 → 需要确认且不自动重试。"""
    target = (action or {}).get("target") or {}
    text = f"{target.get('value', '')} {target.get('name', '')}".lower()
    return any(h in text for h in _SIDE_EFFECT_HINTS)
