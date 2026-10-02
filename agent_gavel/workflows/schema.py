"""工作流模板 schema（docs/tech-plan.md §5.15.1）。

新格式 schema_version=2。提供：校验、参数替换、checkpoint→断言 checks 归一。
"""

import copy
import re

from ..runtime.errors import GavelError

SCHEMA_VERSION = 2

ACTION_TYPES = {
    "navigate", "click", "fill", "type", "press", "check", "uncheck", "select",
    "hover", "focus", "clear", "scroll", "upload", "drag",
    "wait_for_selector", "wait_for_url", "expect", "screenshot", "pdf",
}
_TARGET_ACTIONS = {"click", "fill", "type", "press", "check", "uncheck",
                   "select", "hover", "focus", "clear", "scroll", "upload",
                   "drag"}
# 语义上可重复执行而不产生新副作用的动作（重试/修复默认只对这些放行）。
# 注意：click/press/type/drag 不在此列——它们可能提交/追加，默认不可自动重试。
_INHERENTLY_IDEMPOTENT = {"navigate", "fill", "select", "check", "uncheck",
                          "hover", "focus", "clear", "scroll", "upload"}
_SIDE_EFFECT_HINTS = ("提交", "保存", "删除", "支付", "付款", "购买", "下单",
                      "确认", "submit", "save", "delete", "pay", "buy",
                      "purchase", "confirm", "send")

_VAR_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def substitute(obj, params):
    """把 $VAR 占位符替换成 params 实际值（递归）。

    用变量名整体匹配，避免前缀冲突：$QUERY 不会误替换进 $QUERY_ID；
    params 里没有的变量保持原样（不静默清空）。
    """
    params = params or {}

    def rec(x):
        if isinstance(x, dict):
            return {k: rec(v) for k, v in x.items()}
        if isinstance(x, list):
            return [rec(v) for v in x]
        if isinstance(x, str):
            return _VAR_RE.sub(
                lambda m: str(params.get(m.group(1), m.group(0))), x)
        return x

    return rec(obj)


def extract_vars(obj):
    found = []

    def rec(x):
        if isinstance(x, dict):
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)
        elif isinstance(x, str):
            for m in _VAR_RE.finditer(x):
                if m.group(1) not in found:
                    found.append(m.group(1))

    rec(obj)
    return found


def norm_parameters(template):
    """把 parameters 规整成 {name: {type, sensitive}}，并补 steps 里出现的 $VAR。"""
    raw = template.get("parameters")
    out = {}
    if isinstance(raw, dict):
        for name, spec in raw.items():
            if isinstance(spec, dict):
                out[name] = {"type": spec.get("type", "string"),
                             "sensitive": bool(spec.get("sensitive"))}
            else:
                out[name] = {"type": "string", "sensitive": bool(spec)}
    elif isinstance(raw, list):
        for name in raw:
            out[name] = {"type": "string", "sensitive": False}
    for name in extract_vars(template.get("steps")):
        out.setdefault(name, {"type": "string", "sensitive": False})
    return out


def checkpoint_to_checks(cp):
    """把 checkpoint 描述归一成断言 checks（dict 或 list）。"""
    if not cp:
        return None
    if isinstance(cp, dict) and "checks" in cp:
        return cp["checks"]
    if isinstance(cp, dict) and "op" in cp:
        return cp
    if not isinstance(cp, dict):
        return None
    t = cp.get("type")
    val = cp.get("value")
    sel = cp.get("selector")
    mapping = {
        "url_contains": {"read": "url", "op": "contains", "value": val},
        "url_eq": {"read": "url", "op": "eq", "value": val},
        "url_matches": {"read": "url", "op": "regex", "value": val},
        "title_contains": {"read": "title", "op": "contains", "value": val},
        "text_contains": {"read": "text", "selector": sel, "op": "contains",
                          "value": val},
        "text_eq": {"read": "text", "selector": sel, "op": "eq", "value": val},
        "value_eq": {"read": "value", "selector": sel, "op": "eq", "value": val},
        "exists": {"read": "count", "selector": sel, "op": "exists"},
        "not_exists": {"read": "count", "selector": sel, "op": "not_exists"},
        "count_eq": {"read": "count", "selector": sel, "op": "count_eq",
                     "value": val},
    }
    if t in mapping:
        chk = mapping[t]
        if cp.get("frame"):
            chk["frame"] = cp["frame"]
        return {"checkpoint": chk}
    return cp


def _looks_side_effect(action):
    text = ""
    target = action.get("target") or {}
    text = str(target.get("value", "")) + " " + str(target.get("name", ""))
    low = text.lower()
    return any(h in text or h in low for h in _SIDE_EFFECT_HINTS)


def validate(template):
    """校验并归一；返回 (normalized, errors, warnings)。"""
    errors, warnings = [], []
    if not isinstance(template, dict):
        return None, ["template 必须是对象"], []
    t = copy.deepcopy(template)
    t["schema_version"] = SCHEMA_VERSION
    tid = t.get("template_id")
    if not tid:
        errors.append("缺少 template_id")
    elif not _ID_RE.match(str(tid)):
        errors.append("template_id 只允许小写字母/数字/下划线"
                      f"（收到 {tid!r}）")
    steps = t.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps 必须是非空数组")
        steps = []
    norm_steps = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            errors.append(f"steps[{i}] 不是对象")
            continue
        act = st.get("action")
        if not isinstance(act, dict) or "type" not in act:
            errors.append(f"steps[{i}] 缺少 action.type")
            continue
        atype = act["type"]
        if atype not in ACTION_TYPES:
            errors.append(f"steps[{i}] 未知 action.type：{atype}")
            continue
        # 归一：type/fill 用 text 传值的一律落到 value（避免"没输入却报成功"）
        if atype in ("type", "fill") and "value" not in act \
                and act.get("text") is not None:
            act["value"] = act["text"]
        if atype in _TARGET_ACTIONS and not act.get("target"):
            errors.append(f"steps[{i}] action={atype} 缺少 target")
        if atype == "navigate" and not act.get("url"):
            errors.append(f"steps[{i}] navigate 缺少 url")
        if atype in ("fill", "type") and "value" not in act:
            errors.append(f"steps[{i}] {atype} 缺少 value")
        step = {
            "id": st.get("id") or f"step{i + 1}",
            "action": act,
        }
        if st.get("checkpoint"):
            step["checkpoint"] = st["checkpoint"]
        # 可重试性：默认按"动作类型是否天然幂等"判断，而不是"没命中副作用关键词
        # 就当安全"。click/press/type/drag 默认不可自动重试；命中副作用关键词的
        # 还需确认。显式声明的值优先，并记录 retry_explicit 供执行器区分。
        side = _looks_side_effect(act)
        explicit = ("idempotent" in st) or ("safe_to_retry" in st)
        inherent = (atype in _INHERENTLY_IDEMPOTENT) and not side
        step["idempotent"] = bool(st.get("idempotent", inherent))
        step["safe_to_retry"] = bool(st.get("safe_to_retry", inherent))
        step["requires_confirmation"] = bool(
            st.get("requires_confirmation", side))
        step["retry_explicit"] = explicit
        if side:
            warnings.append(f"steps[{i}]（{step['id']}）疑似副作用动作，"
                            f"标记为需确认/不可自动重试")
        norm_steps.append(step)
    t["steps"] = norm_steps
    t["parameters"] = norm_parameters(t)
    t.setdefault("site", "")
    t.setdefault("desc", "")
    t.setdefault("backend_requirements", ["semantic"])
    t.setdefault("preconditions", [])
    t.setdefault("outputs", [])
    t.setdefault("failure_policy", {"retry": "safe_only",
                                    "repair": "explore_local_step"})
    t.setdefault("stats", {"success_count": 0, "fail_count": 0,
                           "last_status": None, "last_at": None})
    t.setdefault("version", 1)
    if errors:
        return None, errors, warnings
    return t, [], warnings


async def check_preconditions(template, page):
    """返回 (ok, detail)。支持 url_contains/url_matches/selector_exists。"""
    results = []
    for pc in template.get("preconditions", []):
        t = pc.get("type")
        val = pc.get("value")
        url = page.url
        if t == "url_contains":
            ok = val in url
        elif t == "url_matches":
            ok = bool(re.search(str(val), url))
        elif t == "selector_exists":
            try:
                ok = await page.locator(pc.get("selector", "")).count() > 0
            except Exception:
                ok = False
        else:
            ok = True
        results.append({"type": t, "value": val, "ok": ok})
    return all(r["ok"] for r in results), results
