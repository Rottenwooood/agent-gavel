"""断言求值：Python 读取/比较，以及同一 IR 生成的浏览器侧谓词。

`read=diff` 读的是 Observation 采集到的变化项（`path` 为点路径，
如 `pages.opened`、`artifacts`）。它在 Python 侧求值，浏览器侧无对应谓词。
"""

import re
import time

from ...core.checks import Check, parse, read_kind
from ...core.errors import GavelError
from .frames import frame_root
from .jsrender import predicate

_COUNT_OPS = frozenset({"count_eq", "count_gte", "count_gt", "exists", "not_exists"})


def _dig(obj, path):
    """按点路径取值；列表用数字下标。路径不存在返回 None（不是 0）。"""
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


async def _read_url(ctx, check, root):
    return ctx["page"].url


async def _read_title(ctx, check, root):
    return await ctx["page"].title()


async def _read_expr(ctx, check, root):
    return await ctx["page"].evaluate(check.expr)


async def _read_count(ctx, check, root):
    return await root.locator(check.selector or "").count()


async def _read_text(ctx, check, root):
    if not check.selector:
        page = ctx["page"]
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    loc = root.locator(check.selector).first
    try:
        return (await loc.inner_text()).strip()
    except Exception:
        return (await loc.text_content() or "").strip()


async def _read_value(ctx, check, root):
    return await root.locator(check.selector or "").first.input_value()


async def _read_attr(ctx, check, root):
    return await root.locator(check.selector or "").first.get_attribute(check.attr or "")


async def _read_html(ctx, check, root):
    return await root.locator(check.selector or "").first.inner_html()


async def _read_diff(ctx, check, root):
    value = _dig(ctx.get("obs") or {}, check.path or "")
    if check.op in _COUNT_OPS and isinstance(value, (list, dict)):
        return len(value)
    return value


READERS = {
    "url": _read_url,
    "title": _read_title,
    "text": _read_text,
    "value": _read_value,
    "count": _read_count,
    "html": _read_html,
    "expr": _read_expr,
    "attr": _read_attr,
    "diff": _read_diff,
}


async def read_value(ctx, check):
    page = ctx["page"]
    root = frame_root(page, check.frame)
    if check.op in ("exists", "not_exists"):
        return await root.locator(check.selector or "").count()
    read = read_kind(check)
    if read not in READERS:
        raise GavelError(
            "bad_check",
            f"无法判断该 check 要读什么：{check.to_dict()}",
            hint="用 read/name/path 指定 url|title|text|value|count|expr|attr|html|diff",
        )
    return await READERS[read](ctx, check, root)


def _cmp_eq(actual, expected):
    return actual == expected


def _cmp_neq(actual, expected):
    return actual != expected


def _cmp_contains(actual, expected):
    return expected is not None and str(expected) in str(actual)


def _cmp_not_contains(actual, expected):
    return expected is None or str(expected) not in str(actual)


def _cmp_regex(actual, expected):
    return bool(re.search(str(expected), str(actual)))


def _cmp_gt(actual, expected):
    return actual > expected


def _cmp_lt(actual, expected):
    return actual < expected


def _cmp_gte(actual, expected):
    return actual >= expected


def _cmp_lte(actual, expected):
    return actual <= expected


def _cmp_exists(actual, expected):
    return (actual or 0) > 0


def _cmp_not_exists(actual, expected):
    return (actual or 0) == 0


COMPARATORS = {
    "eq": _cmp_eq,
    "neq": _cmp_neq,
    "contains": _cmp_contains,
    "not_contains": _cmp_not_contains,
    "regex": _cmp_regex,
    "gt": _cmp_gt,
    "lt": _cmp_lt,
    "gte": _cmp_gte,
    "lte": _cmp_lte,
    "exists": _cmp_exists,
    "not_exists": _cmp_not_exists,
    "count_eq": _cmp_eq,
    "count_gte": _cmp_gte,
    "count_gt": _cmp_gt,
}


def compare(op, actual, expected):
    try:
        fn = COMPARATORS[op]
    except KeyError:
        raise GavelError("bad_op", f"未知断言 op：{op}") from None
    try:
        return fn(actual, expected)
    except TypeError:
        return False


async def _run_checks(ctx, checks):
    results = []
    for check in checks:
        name = check.name or check.read or "check"
        try:
            actual = await read_value(ctx, check)
            passed = compare(check.op, actual, check.expected)
            results.append(
                {
                    "name": name,
                    "op": check.op,
                    "expected": check.expected,
                    "actual": actual,
                    "passed": passed,
                }
            )
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "name": name,
                    "op": check.op,
                    "expected": check.expected,
                    "actual": None,
                    "passed": False,
                    "error": str(e),
                }
            )
    return results


async def evaluate(backend, handle, checks, obs=None, wait_s=0.0, interval=0.1, target=None):
    """三态求值：pass / fail / ambiguous。

    ambiguous 的证据来自 `obs["dom"]["meaningful"]` —— 由 Observation 在动作
    期间采集，不再在这里重算签名。
    """
    parsed = (
        checks
        if isinstance(checks, list) and all(isinstance(c, Check) for c in checks)
        else parse(checks)
    )
    if not parsed:
        return {"status": "pass", "assertions": []}

    page = handle.page
    ctx = {"page": page, "obs": obs}
    deadline = time.monotonic() + max(0.0, wait_s)
    if wait_s and wait_s > 0 and not (target and target.get("frame")):
        pred = predicate(parsed)
        if pred is not None:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                await page.wait_for_function(pred, timeout=int(remaining * 1000))
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    last = None
    while True:
        last = await _run_checks(ctx, parsed)
        if all(item["passed"] for item in last) or time.monotonic() >= deadline:
            break
        await _sleep(interval)

    if all(item["passed"] for item in last):
        return {"status": "pass", "assertions": last}
    if isinstance(obs, dict) and (obs.get("dom") or {}).get("meaningful"):
        return {"status": "ambiguous", "assertions": last, "diff": obs}
    return {"status": "fail", "assertions": last}


async def _sleep(seconds):
    import asyncio

    await asyncio.sleep(seconds)
