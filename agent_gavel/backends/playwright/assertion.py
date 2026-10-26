"""断言求值：Python 读取/比较，以及同一 IR 生成的浏览器侧谓词。"""

import re
import time

from ...core.checks import Check, parse, read_kind
from ...core.errors import GavelError
from .frames import frame_root
from .jsrender import predicate
from .signature import diff_summary, page_signature


async def _read_url(page, check, root):
    return page.url


async def _read_title(page, check, root):
    return await page.title()


async def _read_expr(page, check, root):
    return await page.evaluate(check.expr)


async def _read_count(page, check, root):
    return await root.locator(check.selector or "").count()


async def _read_text(page, check, root):
    if not check.selector:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    loc = root.locator(check.selector).first
    try:
        return (await loc.inner_text()).strip()
    except Exception:
        return (await loc.text_content() or "").strip()


async def _read_value(page, check, root):
    return await root.locator(check.selector or "").first.input_value()


async def _read_attr(page, check, root):
    return await root.locator(check.selector or "").first.get_attribute(check.attr or "")


async def _read_html(page, check, root):
    return await root.locator(check.selector or "").first.inner_html()


READERS = {
    "url": _read_url,
    "title": _read_title,
    "text": _read_text,
    "value": _read_value,
    "count": _read_count,
    "html": _read_html,
    "expr": _read_expr,
    "attr": _read_attr,
}


async def read_value(page, check):
    root = frame_root(page, check.frame)
    if check.op in ("exists", "not_exists"):
        return await root.locator(check.selector or "").count()
    read = read_kind(check)
    if read not in READERS:
        raise GavelError(
            "bad_check",
            f"无法判断该 check 要读什么：{check.to_dict()}",
            hint="用 read/name 指定 url|title|text|value|count|expr|attr|html，或给 selector",
        )
    return await READERS[read](page, check, root)


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


async def _run_checks(page, checks):
    results = []
    for check in checks:
        name = check.name or check.read or "check"
        try:
            actual = await read_value(page, check)
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


async def evaluate(backend, handle, checks, before_sig=None, wait_s=0.0, interval=0.1, target=None):
    parsed = (
        checks
        if isinstance(checks, list) and all(isinstance(c, Check) for c in checks)
        else parse(checks)
    )
    if not parsed:
        return {"status": "pass", "assertions": []}

    page = handle.page
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
        last = await _run_checks(page, parsed)
        if all(item["passed"] for item in last) or time.monotonic() >= deadline:
            break
        await _sleep(interval)

    if all(item["passed"] for item in last):
        return {"status": "pass", "assertions": last}
    if before_sig is None:
        return {"status": "fail", "assertions": last}
    after = await page_signature(backend, handle, target=target)
    diff = diff_summary(before_sig, after)
    if diff.get("meaningful"):
        return {"status": "ambiguous", "assertions": last, "diff": diff}
    return {"status": "fail", "assertions": last}


async def _sleep(seconds):
    import asyncio

    await asyncio.sleep(seconds)
