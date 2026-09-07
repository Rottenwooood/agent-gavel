"""归一化：把 AT-SPI 节点列表压成可比较的稳定签名。

目标：剥离动态字段（坐标、焦点、滚动、光标、时间戳类文本），
保留结构信息（role + 稳定语义字段），让前后两次抓取只在真正
的结构变化上产生 diff。

输入：computer-use-linux `state` 返回的扁平节点列表。
输出：dict，包含 normalized_nodes（list）和 signature（str，用于
    稳定等待的快速相等判断）。
"""

import hashlib
import json

# 判定为"动态"的字段——归一化时直接丢弃，不参与签名。
_DYNAMIC_FIELDS = {
    "bounds", "states", "actions", "depth", "child_count",
    "parent_index", "index", "object_ref", "description",
}

# 某些 role 的 text 几乎必然是动态的（时钟、秒表、进度），
# 丢弃其文本以防持续触发 diff。
_VOLATILE_ROLES = {
    "panel", "timer", "progressbar", "clock", "label", "statusbar",
}

# 判定为"结构噪音"的 role——元素树里频繁出现但无信息量。
_NOISE_ROLES = {"separator", "unknown", "empty", "decorator"}


def _clean_text(value):
    if value is None:
        return None
    # 若是 dict（如 Firefox 的 {caret_offset, character_count, content}），
    # 只取 content，且 content 对 diff 而言是动态的，置空以抑制噪音
    if isinstance(value, dict):
        content = value.get("content")
        if content is None:
            return None
        text = str(content)
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    if not text:
        return None
    return text


def _is_volatile(node):
    role = node.get("role") or ""
    if role in _VOLATILE_ROLES:
        return True
    return False


def _node_key(node):
    """稳定身份：role + name + value，用于跨快照对应元素。"""
    role = node.get("role") or ""
    name = _clean_text(node.get("name"))
    value = _clean_text(node.get("value"))
    return json.dumps([role, name, value], ensure_ascii=False)


def normalize_nodes(nodes):
    """输入 state 的节点列表，输出归一化列表 + 整体签名。"""
    kept = []
    for node in nodes or []:
        role = node.get("role") or "unknown"
        if role in _NOISE_ROLES:
            continue
        if _is_volatile(node):
            # 动态角色：保留结构，但文本不参与签名
            entry = {
                "role": role,
                "name": None,
                "value": None,
                "text": None,
            }
        else:
            entry = {
                "role": role,
                "name": _clean_text(node.get("name")),
                "value": _clean_text(node.get("value")),
                # text 动态性强，不参与签名；保留仅供断言调试
                "text": None,
            }
        kept.append(entry)
    # 排序以便位置无关比较（不依赖遍历顺序）
    kept.sort(key=lambda e: _node_key(e))
    signature = hashlib.sha256(
        json.dumps(kept, ensure_ascii=False).encode()
    ).hexdigest()
    return {"normalized": kept, "signature": signature}


def quick_hash(nodes):
    return normalize_nodes(nodes)["signature"]


def snapshot_from_state(state_output):
    """兼容：直接接收 state 命令的 stdout（JSON 列表）或已解析对象。"""
    if isinstance(state_output, str):
        import json as _json
        data = _json.loads(state_output)
    else:
        data = state_output
    return normalize_nodes(data)
