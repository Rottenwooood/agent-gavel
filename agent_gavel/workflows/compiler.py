"""工作流编译：录制步骤 → 模板草稿（docs/tech-plan.md §5.15.2）。

处理：删无效动作、合并连续输入、固定值→参数、敏感参数、补关键检查点、
标记不可自动重试动作、评估后端需求、生成版本。
"""

from urllib.parse import urlparse

from . import schema as _schema

_PURE_ACTIONS = {"hover", "focus"}


def _action_of(step):
    return step.get("action") or {}


def _same_target(a, b):
    return (a or {}).get("target") == (b or {}).get("target") and a.get("target")


def compile(recorded_steps, *, template_id, site="", desc="",
            parameterize=None, sensitive=None):
    """recorded_steps: [{"action": {...}, "url_before", "url_after"}, ...]"""
    parameterize = parameterize or {}
    sensitive = set(sensitive or [])

    # 1) 删无效动作（纯 hover/focus）
    steps = [s for s in recorded_steps
             if _action_of(s).get("type") not in _PURE_ACTIONS]

    # 2) 合并连续输入（type→type 合并为 fill）
    merged = []
    i = 0
    while i < len(steps):
        cur = steps[i]
        act = dict(_action_of(cur))
        if act.get("type") == "type" and i + 1 < len(steps) \
                and _action_of(steps[i + 1]).get("type") == "type" \
                and _same_target(act, _action_of(steps[i + 1])):
            text = str(act.get("text", "")) + str(
                _action_of(steps[i + 1]).get("text", ""))
            nxt = steps[i + 1]
            cur = {"action": {"type": "fill", "target": act.get("target"),
                              "value": text},
                   "url_before": cur.get("url_before"),
                   "url_after": nxt.get("url_after")}
            i += 2
        else:
            i += 1
        merged.append(cur)
    steps = merged

    # 3) 参数化 + 4) 敏感标记
    params = {}
    for name in parameterize:
        params[name] = {"type": "string",
                        "sensitive": name in sensitive or name.upper() in sensitive}

    def _param_value(v):
        for name, actual in parameterize.items():
            if str(actual) == str(v):
                return f"${name}"
        return v

    out_steps = []
    for idx, st in enumerate(steps):
        act = dict(_action_of(st))
        if "value" in act:
            act["value"] = _param_value(act["value"])
        if "text" in act:
            act["text"] = _param_value(act["text"])
        if "url" in act:
            act["url"] = _param_value(act["url"])
        step = {"id": f"step{idx + 1}", "action": act}
        # 5) 补关键检查点：url 变化 → url_contains(path)
        if st.get("url_before") and st.get("url_after") \
                and st["url_before"] != st["url_after"]:
            p = urlparse(st["url_after"])
            token = (p.path or "").rstrip("/").split("/")[-1] or p.netloc
            if token:
                step["checkpoint"] = {"type": "url_contains", "value": token}
        out_steps.append(step)

    draft = {
        "schema_version": _schema.SCHEMA_VERSION,
        "template_id": template_id,
        "version": 1,
        "site": site,
        "desc": desc,
        "parameters": params,
        "steps": out_steps,
        "backend_requirements": ["semantic"],
        "preconditions": [],
        "outputs": [],
        "failure_policy": {"retry": "safe_only",
                           "repair": "explore_local_step"},
    }
    normalized, errors, warnings = _schema.validate(draft)
    return normalized or draft, errors, warnings
