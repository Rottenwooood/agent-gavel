"""断言 IR：读取类型、比较操作、唯一解析入口。

所有断言写法（映射 / 扁平 / 顶层 op / 单条字段 / checkpoint）都先解析成
`Check`，再交给 backend 生成 Python 求值与浏览器侧谓词。其他模块不得再
自行推导"读什么、怎么比"。
"""

from dataclasses import dataclass

from .errors import GavelError

READ_KINDS = frozenset({"url", "title", "text", "value", "count", "html",
                        "expr", "attr"})
OPS = frozenset({"eq", "neq", "contains", "not_contains", "regex", "exists",
                 "not_exists", "gt", "lt", "gte", "lte", "count_eq",
                 "count_gte", "count_gt"})

_READ_PREFIX = {"url": "url", "title": "title", "text": "text",
                "value": "value", "count": "count", "html": "html",
                "expr": "expr", "attr": "attr", "selector": "text"}
_OP_SUFFIXES = ("not_contains", "count_gte", "count_gt", "count_eq",
                "contains", "not_exists", "exists", "neq", "eq", "regex",
                "gte", "lte", "gt", "lt")
_CHECK_FIELDS = ("read", "selector", "expr", "value", "expected")


@dataclass(frozen=True)
class Check:
    name: str = None
    read: str = None
    op: str = "eq"
    expected: object = None
    selector: str = None
    attr: str = None
    expr: str = None
    frame: object = None

    def to_dict(self):
        return {k: v for k, v in {
            "name": self.name, "read": self.read, "op": self.op,
            "expected": self.expected, "selector": self.selector,
            "attr": self.attr, "expr": self.expr, "frame": self.frame,
        }.items() if v is not None}


def _check(**kwargs):
    return Check(**kwargs)


def _parse_one(name, value):
    read, op = None, "eq"
    for suffix in _OP_SUFFIXES:
        if name.endswith("_" + suffix) and name[:-len(suffix) - 1] in _READ_PREFIX:
            read = _READ_PREFIX[name[:-len(suffix) - 1]]
            op = suffix
            break
    if isinstance(value, dict):
        item = dict(value)
        expected = item.pop("expected", item.pop("value", None))
        item.pop("value", None)
        read = item.pop("read", read)
        op = item.pop("op", op)
        frame = item.pop("frame", None)
        selector = item.pop("selector", None)
        attr = item.pop("attr", None)
        expr = item.pop("expr", None)
        if read is None:
            if expr is not None:
                read = "expr"
            elif attr:
                read = "attr"
            elif selector:
                read = "text"
        if item:
            raise GavelError("bad_check", f"未识别的断言字段：{sorted(item)}",
                             hint="用 read/op/value/selector/attr/expr/frame")
        return _check(name=name, read=read, op=op, expected=expected,
                      selector=selector, attr=attr, expr=expr, frame=frame)
    return _check(name=name, read=read, op=op, expected=value)


def parse(raw):
    """把任意断言写法解析成 list[Check]。"""
    if raw is None:
        return []
    if isinstance(raw, Check):
        return [raw]
    if isinstance(raw, dict):
        if any(k in raw for k in _CHECK_FIELDS):
            item = dict(raw)
            expected = item.pop("expected", item.pop("value", None))
            item.pop("value", None)
            read = item.pop("read", None)
            selector = item.pop("selector", None)
            attr = item.pop("attr", None)
            expr = item.pop("expr", None)
            op = item.pop("op", "eq")
            frame = item.pop("frame", None)
            name = item.pop("name", None) or read or "check"
            if item:
                raise GavelError("bad_check",
                                 f"未识别的断言字段：{sorted(item)}")
            if read is None:
                if expr is not None:
                    read = "expr"
                elif attr:
                    read = "attr"
                elif selector:
                    read = "text"
            return [_check(name=name, read=read, op=op, expected=expected,
                           selector=selector, attr=attr, expr=expr,
                           frame=frame)]
        if "op" in raw:
            keys = [k for k in raw if k in _READ_PREFIX]
            if len(keys) == 1:
                key = keys[0]
                return [_check(name=key, read=_READ_PREFIX[key],
                               op=raw["op"], expected=raw[key])]
            raise GavelError("bad_check", f"顶层 op 无法绑定目标：{raw}")
        return [_parse_one(name, value) for name, value in raw.items()]
    if isinstance(raw, list):
        out = []
        for item in raw:
            out.extend(parse(item))
        return out
    raise GavelError("bad_check", f"不支持的断言形态：{type(raw).__name__}")


_CHECKPOINT_OPS = {
    "url_contains": ("url", "url", "contains"),
    "url_eq": ("url", "url", "eq"),
    "url_matches": ("url", "url", "regex"),
    "title_contains": ("title", "title", "contains"),
    "text_contains": ("text", "text", "contains"),
    "text_eq": ("text", "text", "eq"),
    "value_eq": ("value", "value", "eq"),
    "exists": ("exists", "count", "exists"),
    "not_exists": ("not_exists", "count", "not_exists"),
    "count_eq": ("count_eq", "count", "count_eq"),
}


def from_checkpoint(cp):
    """checkpoint 描述 → list[Check]；未知类型直接报错，不静默放行。"""
    if not cp:
        return []
    if isinstance(cp, dict) and "checks" in cp:
        return parse(cp["checks"])
    if isinstance(cp, dict) and "op" in cp:
        return parse(cp)
    if not isinstance(cp, dict):
        raise GavelError("bad_checkpoint", f"checkpoint 必须是对象：{cp!r}")
    ctype = cp.get("type")
    if ctype not in _CHECKPOINT_OPS:
        raise GavelError("bad_checkpoint", f"未知 checkpoint 类型：{ctype}")
    name, read, op = _CHECKPOINT_OPS[ctype]
    return [_check(name=name, read=read, op=op, expected=cp.get("value"),
                   selector=cp.get("selector"), frame=cp.get("frame"))]
