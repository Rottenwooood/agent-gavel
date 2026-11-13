"""把 Check IR 渲染成浏览器侧谓词。"""

import json

from ...core.checks import read_kind


def _js_quote(value):
    return json.dumps(value)


def _js_value(check, selector):
    read = read_kind(check)
    if check.op in ("exists", "not_exists"):
        read = "count"
    if read == "url":
        return "location.href"
    if read == "title":
        return "document.title"
    if read == "expr":
        if check.expr is None:
            return None
        return "(" + check.expr + ")"
    if read == "count":
        return f"document.querySelectorAll({_js_quote(selector or '')}).length"
    if not selector and read != "text":
        return None
    if read == "value":
        return f"(__q({_js_quote(selector or '')})?__q({_js_quote(selector or '')}).value:null)"
    if read == "attr":
        return (
            f"(__q({_js_quote(selector or '')})?"
            f"__q({_js_quote(selector or '')}).getAttribute("
            f"{_js_quote(check.attr or '')}):null)"
        )
    if read == "html":
        return f"(__q({_js_quote(selector or '')})?__q({_js_quote(selector or '')}).innerHTML:null)"
    if not selector:
        return "(document.body?document.body.innerText:'')"
    return (
        f"((__q({_js_quote(selector)})&&(__q({_js_quote(selector)}).innerText"
        f"||__q({_js_quote(selector)}).textContent))||'').trim()"
    )


def _js_compare(op, actual, expected):
    if op == "exists":
        return f"(({actual})||0) > 0"
    if op == "not_exists":
        return f"(({actual})||0) === 0"
    if op == "eq":
        return f"({actual}) === {expected}"
    if op == "neq":
        return f"({actual}) !== {expected}"
    if op == "contains":
        return f"String({actual}).indexOf(String({expected})) !== -1"
    if op == "not_contains":
        return f"String({actual}).indexOf(String({expected})) === -1"
    if op == "regex":
        return f"new RegExp(String({expected})).test(String({actual}))"
    if op in ("gt", "lt", "gte", "lte"):
        sym = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}[op]
        return f"({actual}) {sym} {expected}"
    if op == "count_eq":
        return f"({actual}) === {expected}"
    if op == "count_gte":
        return f"({actual}) >= {expected}"
    if op == "count_gt":
        return f"({actual}) > {expected}"
    return None


def render_js(check):
    """单条 Check 的浏览器侧谓词；不支持时返回 None（调用方退回轮询）。

    `read=diff` 在 Python 侧求值，浏览器侧没有可等待的谓词，恒真即可
    （避免无谓地耗尽 wait_s）。
    """
    if check.frame:
        return None
    if read_kind(check) == "diff":
        return "true"
    expr = _js_value(check, check.selector)
    if expr is None:
        return None
    expected = _js_quote(check.expected)
    return _js_compare(check.op, expr, expected)


def predicate(checks):
    parts = [render_js(check) for check in checks]
    if not parts or any(part is None for part in parts):
        return None
    body = " && ".join(f"({part})" for part in parts)
    return f"() => {{ const __q=s=>s?document.querySelector(s):null; return ({body}); }}"
