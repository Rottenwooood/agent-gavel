"""结构 diff：对比两个归一化快照，产出新增/消失/值变。

比较单位：元素签名（role+name+value）。
- 只在前快照不在后快照 => 消失
- 只在后快照不在前快照 => 新增
- 两侧都在但 value 不同 => 值变

返回结构化 diff 摘要 + 一个"是否发生了有意义变化"的判定。
"""

from collections import Counter


def _element_signature(entry):
    return (entry["role"], entry["name"], entry["value"])


def diff_snapshots(before, after):
    """before/after 是 normalize_nodes 的 normalized 列表。"""
    b = Counter(_element_signature(e) for e in before)
    a = Counter(_element_signature(e) for e in after)

    removed = []
    added = []
    changed = []

    for sig, cnt in b.items():
        acnt = a.get(sig, 0)
        if acnt < cnt:
            removed.append(_sig_to_obj(sig, cnt - acnt))
    for sig, cnt in a.items():
        bcnt = b.get(sig, 0)
        if bcnt < cnt:
            added.append(_sig_to_obj(sig, cnt - bcnt))

    # 值变：同 role+name，value 不同
    b_by_rn = _group_by_role_name(before)
    a_by_rn = _group_by_role_name(after)
    for rn, bvals in b_by_rn.items():
        if rn not in a_by_rn:
            continue
        avals = a_by_rn[rn]
        for v in bvals:
            if v not in avals:
                changed.append({"role": rn[0], "name": rn[1],
                                "value_before": v, "value_after": None})
        for v in avals:
            if v not in bvals:
                changed.append({"role": rn[0], "name": rn[1],
                                "value_before": None, "value_after": v})

    total = len(removed) + len(added) + len(changed)
    return {
        "removed": removed,
        "added": added,
        "changed": changed,
        "total": total,
    }


def _sig_to_obj(sig, count):
    return {"role": sig[0], "name": sig[1], "value": sig[2], "count": count}


def _group_by_role_name(entries):
    out = {}
    for e in entries:
        key = (e["role"], e["name"])
        out.setdefault(key, set()).add(e["value"])
    return out


def meaningful_change(diff, min_structural=1):
    """是否有'值得上报'的变化。

    纯坐标/焦点/文本噪音已被归一化剥掉，所以这里只看结构计数。
    min_structural: 至少多少个结构单元变化才算有变化。
    """
    return diff["total"] >= min_structural


def classify(diff):
    """把 diff 判成一个标签，供 auto mode 使用。"""
    if diff["total"] == 0:
        return "no_change"
    # 有新增 => 最可能"页面前进/新元素出现"
    if diff["added"] and not diff["removed"]:
        return "added_only"
    if diff["removed"] and not diff["added"]:
        return "removed_only"
    return "mixed"
