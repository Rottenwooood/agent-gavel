"""Locator input grammar and normalization, shared by schemas and execution."""

from .errors import GavelError

LOCATOR_KINDS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")


def normalize(target):
    if not isinstance(target, dict):
        raise GavelError("bad_target", "target 必须是对象", hint='如 {"by":"css","value":"#q"}')
    by, value = target.get("by"), target.get("value")
    if by is None:
        for key in LOCATOR_KINDS:
            if key in target:
                by, value = key, target[key]
                break
    if by not in LOCATOR_KINDS or value is None:
        raise GavelError(
            "bad_target", "需要有效的 by/value 或定位短写", hint="by: " + "|".join(LOCATOR_KINDS)
        )
    return by, value


def schema():
    nullable_string = {"type": ["string", "null"]}
    fields = {
        "by": {"enum": list(LOCATOR_KINDS)},
        "value": {"type": "string"},
        "name": nullable_string,
        "exact": {"type": ["boolean", "null"]},
        "strict": {"type": ["boolean", "null"], "default": True},
        "nth": {"type": ["integer", "null"]},
        "has_text": nullable_string,
        "frame": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "null"},
            ]
        },
        **{key: {"type": "string"} for key in LOCATOR_KINDS},
    }
    return {
        "type": "object",
        "properties": fields,
        "anyOf": [{"required": ["by", "value"]}] + [{"required": [key]} for key in LOCATOR_KINDS],
    }
