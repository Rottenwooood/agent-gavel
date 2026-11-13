"""工作流编译：录制步骤 → 模板草稿（docs/tech-plan.md §5.15.2）。

处理：合并连续输入、固定值→参数（覆盖 action/target/checkpoint 全部字符串）、
敏感参数、保留录制断言、按 diff 自动补关键检查点、标记不可自动重试动作、
保留页面相对引用、生成版本。
"""

from urllib.parse import urlparse

from . import schema as _schema


def _action_of(step):
    return step.get("action") or {}


def _same_target(a, b):
    return (a or {}).get("target") == (b or {}).get("target") and a.get("target")


def _literal(spec):
    """parameterize 的两种写法：{NAME: "值"} 或 {NAME: {value/default: "值"}}。"""
    if isinstance(spec, dict):
        return spec.get("value", spec.get("default"))
    return spec


def _param_walk(x, pairs, key=None):
    """递归替换所有字符串叶子里的固定值；`id` 不参与（否则模板 id 会被参数化）。"""
    if key == "id":
        return x
    if isinstance(x, str):
        for name, actual in pairs:
            if actual and actual in x:
                x = x.replace(actual, f"${name}")
        return x
    if isinstance(x, list):
        return [_param_walk(v, pairs) for v in x]
    if isinstance(x, dict):
        return {k: _param_walk(v, pairs, k) for k, v in x.items()}
    return x


def _auto_checkpoint(step):
    """用录到的 diff 生成可断言的检查点；没有可断言信号时退回 url_contains。

    弹新页 / 下载 / 弹窗这些副作用，只有 diff 能表达——这正是自动检查点
    过去几乎为空的原因。
    """
    d = step.get("diff") or {}
    checks = {}
    if (d.get("pages") or {}).get("opened"):
        checks["opened_page"] = {
            "read": "diff",
            "path": "pages.opened",
            "op": "count_gte",
            "value": 1,
        }
    if d.get("artifacts"):
        checks["downloaded"] = {
            "read": "diff",
            "path": "artifacts",
            "op": "count_gte",
            "value": 1,
        }
    if d.get("dialogs"):
        checks["dialog_seen"] = {
            "read": "diff",
            "path": "dialogs",
            "op": "count_gte",
            "value": 1,
        }
    if (
        not checks
        and step.get("url_before")
        and step.get("url_after")
        and step["url_before"] != step["url_after"]
    ):
        p = urlparse(step["url_after"])
        token = (p.path or "").rstrip("/").split("/")[-1] or p.netloc
        if token:
            checks["url_ok"] = {"read": "url", "op": "contains", "value": token}
    return {"checks": checks} if checks else None


def compile(
    recorded_steps,
    *,
    template_id,
    site="",
    desc="",
    parameterize=None,
    sensitive=None,
    auto_checkpoint=False,
):
    """recorded_steps: [{"action", "page", "url_before", "url_after", "diff"}]"""
    parameterize = parameterize or {}
    sensitive = set(sensitive or [])

    # 1) 保留全部录制动作（不删 hover/focus——悬停可能展开菜单，focus 也可能是
    #    流程必要动作；是否冗余交给作者在草稿里删）
    steps = list(recorded_steps)

    # 2) 合并连续输入（同目标 type→type 合并为 fill）
    def _text_of(a):
        return str(a.get("text", a.get("value", "")))

    merged = []
    i = 0
    while i < len(steps):
        cur = steps[i]
        act = dict(_action_of(cur))
        if (
            act.get("type") == "type"
            and i + 1 < len(steps)
            and _action_of(steps[i + 1]).get("type") == "type"
            and _same_target(act, _action_of(steps[i + 1]))
        ):
            text = _text_of(act) + _text_of(_action_of(steps[i + 1]))
            nxt = steps[i + 1]
            cur = {
                "action": {"type": "fill", "target": act.get("target"), "value": text},
                "page": cur.get("page"),
                "url_before": cur.get("url_before"),
                "url_after": nxt.get("url_after"),
                "diff": nxt.get("diff") or cur.get("diff"),
            }
            i += 2
        else:
            i += 1
        merged.append(cur)
    steps = merged

    # 3) 参数化 + 4) 敏感标记
    params = {}
    pairs = []
    for name, spec in parameterize.items():
        lit = _literal(spec)
        default = lit if lit is not None else None
        params[name] = {
            "type": "string",
            "sensitive": name in sensitive or name.upper() in sensitive,
            "default": None if default is None else str(default),
            "desc": spec.get("desc", "") if isinstance(spec, dict) else "",
            "required": bool(spec.get("required", default is None))
            if isinstance(spec, dict)
            else default is None,
        }
        if lit is not None:
            pairs.append((name, str(lit)))
    # 长值优先：避免 $SCHOOL 先把 $SCHOOL_ID 的前缀吃掉
    pairs.sort(key=lambda kv: -len(kv[1]))

    out_steps = []
    for idx, st in enumerate(steps):
        act = _param_walk(dict(_action_of(st)), pairs)
        step = {"id": f"step{idx + 1}", "action": act}
        if st.get("page"):
            step["page"] = st["page"]
        # 5) 保留录制时用过的断言；没有则按 diff 补检查点
        if st.get("checkpoint"):
            step["checkpoint"] = _param_walk(st["checkpoint"], pairs)
        elif auto_checkpoint:
            cp = _auto_checkpoint(st)
            if cp:
                step["checkpoint"] = cp
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
        "failure_policy": {"retry": "safe_only", "repair": "explore_local_step"},
        "task": desc,
        "login_required": False,
        "author": "",
        "license": "",
        "runtime": "",
        "verified_at": None,
        "known_limits": "",
        "status": "draft",
    }
    normalized, errors, warnings = _schema.validate(draft)
    return normalized or draft, errors, warnings
