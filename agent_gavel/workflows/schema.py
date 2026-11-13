"""工作流模板 schema（docs/tech-plan.md §5.15.1）。

新格式 schema_version=2。提供：校验、参数替换、checkpoint→断言 checks 归一。
"""

import copy
import re

from ..core import actions as action_specs
from ..core.errors import GavelError

SCHEMA_VERSION = 2

# 模板状态机（社区分享用）
TEMPLATE_STATUSES = {"draft", "verified", "broken", "deprecated"}

_VAR_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def substitute(obj, params, parameters=None):
    """把 $VAR 占位符替换成实际值（递归）。

    取值顺序：run 时 params → 模板 parameters 里的 default → 报错。
    **不再静默保留 `$VAR` 字面量**（那会把 "$SCHOOL" 真的填进 URL）。
    用变量名整体匹配，避免前缀冲突：$QUERY 不会误替换进 $QUERY_ID。
    """
    params = params or {}
    parameters = parameters or {}

    def resolve(name):
        if name in params and params[name] is not None:
            return str(params[name])
        spec = parameters.get(name)
        if isinstance(spec, dict) and spec.get("default") is not None:
            return str(spec["default"])
        raise GavelError(
            "param_missing",
            f"参数 ${name} 未提供且无默认值",
            hint="在 workflow(op=run) 传 params，或在模板 parameters 里设 default",
        )

    def rec(x):
        if isinstance(x, dict):
            return {k: rec(v) for k, v in x.items()}
        if isinstance(x, list):
            return [rec(v) for v in x]
        if isinstance(x, str):
            return _VAR_RE.sub(lambda m: resolve(m.group(1)), x)
        return x

    return rec(obj)


def var_refs(obj, prefix=""):
    """`$VAR` → 引用点路径清单（`steps[1].action.url` 形式）。

    这是"改一个参数前先看影响面"的唯一依据；不做存储，按需计算。
    """
    out = {}

    def rec(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                rec(v, f"{path}.{k}" if path else k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                rec(v, f"{path}[{i}]")
        elif isinstance(x, str):
            for m in _VAR_RE.finditer(x):
                hits = out.setdefault(m.group(1), [])
                if path not in hits:
                    hits.append(path)

    rec(obj, prefix)
    return out


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
    """把 parameters 规整成 {name: {type, sensitive, default, desc, required}}，
    并补 steps 里出现的 $VAR。default 必须保留，否则 substitute 无法回退。"""
    raw = template.get("parameters")
    out = {}
    if isinstance(raw, dict):
        for name, spec in raw.items():
            if isinstance(spec, dict):
                out[name] = {
                    "type": spec.get("type", "string"),
                    "sensitive": bool(spec.get("sensitive")),
                    "default": spec.get("default"),
                    "desc": spec.get("desc", ""),
                    "required": bool(spec.get("required", spec.get("default") is None)),
                }
            else:
                out[name] = {
                    "type": "string",
                    "sensitive": bool(spec),
                    "default": None,
                    "desc": "",
                    "required": True,
                }
    elif isinstance(raw, list):
        for name in raw:
            out[name] = {
                "type": "string",
                "sensitive": False,
                "default": None,
                "desc": "",
                "required": True,
            }
    for name in extract_vars(template.get("steps")):
        out.setdefault(
            name,
            {"type": "string", "sensitive": False, "default": None, "desc": "", "required": True},
        )
    return out


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
        errors.append(f"template_id 只允许小写字母/数字/下划线（收到 {tid!r}）")
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
        if atype not in action_specs.ACTIONS:
            errors.append(f"steps[{i}] 未知 action.type：{atype}")
            continue
        sp = action_specs.spec(atype)
        # 归一：type/fill 用 text 传值的一律落到 value（避免"没输入却报成功"）
        if atype in ("type", "fill") and "value" not in act and act.get("text") is not None:
            act["value"] = act["text"]
        if sp.target_required and not act.get("target"):
            errors.append(f"steps[{i}] action={atype} 缺少 target")
        for field in sp.required_fields:
            if not act.get(field):
                errors.append(f"steps[{i}] {atype} 缺少 {field}")
        step = {
            "id": st.get("id") or f"step{i + 1}",
            "action": act,
        }
        if st.get("checkpoint"):
            step["checkpoint"] = st["checkpoint"]
        page_ref = st.get("page")
        if page_ref:
            if page_ref != "page0" and not re.match(r"^new:\d+$", str(page_ref)):
                errors.append(f"steps[{i}] page 非法：{page_ref!r}（page0 或 new:N）")
            else:
                step["page"] = page_ref
        if st.get("observe"):
            step["observe"] = True
        # 可重试性：默认按"动作类型是否天然幂等"判断，而不是"没命中副作用关键词
        # 就当安全"。click/press/type/drag 默认不可自动重试；命中副作用关键词的
        # 还需确认。显式声明的值优先，并记录 retry_explicit 供执行器区分。
        side = action_specs.looks_like_side_effect(act)
        explicit = ("idempotent" in st) or ("safe_to_retry" in st)
        inherent = sp.idempotent and not side
        step["idempotent"] = bool(st.get("idempotent", inherent))
        step["safe_to_retry"] = bool(st.get("safe_to_retry", inherent))
        step["requires_confirmation"] = bool(st.get("requires_confirmation", side))
        step["retry_explicit"] = explicit
        if side:
            warnings.append(f"steps[{i}]（{step['id']}）疑似副作用动作，标记为需确认/不可自动重试")
        norm_steps.append(step)
    t["steps"] = norm_steps
    t["parameters"] = norm_parameters(t)
    for name, spec in t["parameters"].items():
        if spec.get("default") is None:
            warnings.append(f"参数 ${name} 无默认值：workflow_run 必须显式传入")
    t.setdefault("site", "")
    t.setdefault("desc", "")
    t.setdefault("backend_requirements", ["semantic"])
    t.setdefault("preconditions", [])
    t.setdefault("outputs", [])
    t.setdefault("failure_policy", {"retry": "safe_only", "repair": "explore_local_step"})
    t.setdefault(
        "stats", {"success_count": 0, "fail_count": 0, "last_status": None, "last_at": None}
    )
    t.setdefault("version", 1)
    # 模板元数据（面向分享/社区）：目标、登录前提、作者/许可、兼容范围、
    # 验证时间、已知限制、状态机。
    t.setdefault("task", "")
    t.setdefault("login_required", False)
    t.setdefault("author", "")
    t.setdefault("license", "")
    t.setdefault("runtime", "")
    t.setdefault("verified_at", None)
    t.setdefault("known_limits", "")
    t.setdefault("status", "draft")
    if t["status"] not in TEMPLATE_STATUSES:
        errors.append(f"status 非法：{t['status']!r}，取 {sorted(TEMPLATE_STATUSES)}")
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
