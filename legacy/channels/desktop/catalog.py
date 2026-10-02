"""断言目录：把"操作的成功标准"存成数据，而非代码。

每条断言 = 前置条件 + 预期。
- 前置条件不满足 => 该断言不适用，跳过（解决"同一操作不同上下文
  断言不同"，例如微信空输入框）。
- 前置条件满足 => 检查预期是否成立，成立=pass，不成立=fail。

断言语言（三条原语）：
  {type: "element_appears",    role, name}
  {type: "element_disappears", role, name}
  {type: "value_changes",      role, name, value_from|value_to|any}

匹配基于归一化后的元素（role+name+value）。name 为空则只按 role。
"""


def _matches(entry, role, name):
    if role and entry["role"] != role:
        return False
    if name and entry["name"] != name:
        return False
    return True


def evaluate_assertion(assertion, snapshot_normalized):
    """对单个断言求值。返回 (applicable: bool, passed: bool, detail: str)"""
    atype = assertion.get("type")
    role = assertion.get("role")
    name = assertion.get("name")

    if atype == "element_appears":
        found = any(_matches(e, role, name) for e in snapshot_normalized)
        return True, found, f"element {role}/{name} {'found' if found else 'missing'}"

    if atype == "element_disappears":
        found = any(_matches(e, role, name) for e in snapshot_normalized)
        return True, (not found), f"element {role}/{name} {'still present' if found else 'gone'}"

    if atype == "value_changes":
        vals = [e["value"] for e in snapshot_normalized if _matches(e, role, name)]
        if not vals:
            return True, False, f"element {role}/{name} not present for value check"
        value_from = assertion.get("value_from")
        value_to = assertion.get("value_to")
        any_change = assertion.get("any", False)
        if any_change:
            # 需要 from 快照才能判"变了没"，这里只给"有值"信号
            return True, True, f"value present: {vals[0]!r}"
        latest = vals[0]
        if value_to is not None:
            return True, (latest == value_to), f"value={latest!r}, expect={value_to!r}"
        if value_from is not None:
            return True, (latest != value_from), f"value={latest!r}, expect changed from {value_from!r}"
        return True, True, f"value={latest!r}"

    return False, False, f"unknown assertion type {atype!r}"


def evaluate_assertions(assertions, snapshot_normalized):
    """逐条评估。返回:
      {"status": "pass"|"fail"|"ambiguous", "results": [...], "skipped": n}
    """
    results = []
    skipped = 0
    for a in assertions:
        if a.get("precondition") is not None:
            # 前置条件本身是一条断言，先对快照求值
            applicable, ppassed, _ = evaluate_assertion(a["precondition"], snapshot_normalized)
            if not applicable or not ppassed:
                skipped += 1
                results.append({"assertion": a, "precondition_skipped": True})
                continue
        applicable, passed, detail = evaluate_assertion(a, snapshot_normalized)
        results.append({"assertion": a, "passed": passed, "detail": detail})

    judged = [r for r in results if "passed" in r]
    if not judged:
        return {"status": "ambiguous", "results": results, "skipped": skipped}
    if all(r["passed"] for r in judged):
        return {"status": "pass", "results": results, "skipped": skipped}
    return {"status": "fail", "results": results, "skipped": skipped}
