"""Generate compact MCP request schemas from the operation contracts."""

import copy
import inspect
import json
from typing import get_type_hints

from playwright.async_api import BrowserContext, ProxySettings, StorageState, ViewportSize
from pydantic import TypeAdapter

from ..core import targets

LIBRARY_INPUTS = {
    "viewport": ViewportSize,
    "proxy": ProxySettings,
    "cookies": get_type_hints(BrowserContext.add_cookies)["cookies"],
    "headers": dict[str, str],
    "extra_http_headers": dict[str, str],
    "page_features": dict[str, str],
    "permissions": list[str],
    "kinds": list[str],
    "sensitive": list[str],
}


def compact_annotations(schema):
    if isinstance(schema, list):
        return [compact_annotations(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for name, value in schema.items():
        if name == "title":
            continue
        if name in ("default", "enum", "const", "examples"):
            result[name] = copy.deepcopy(value)
            continue
        if name in ("properties", "$defs"):
            result[name] = {key: compact_annotations(item) for key, item in value.items()}
        else:
            result[name] = compact_annotations(value)
    return result


def key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def error_hint(error):
    """Describe the contract failure without echoing argument values."""
    if error.validator == "required":
        return "缺少参数：" + ", ".join(
            name for name in error.validator_value if name not in error.instance
        )
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        unknown = set(error.instance) - set(error.schema.get("properties", {}))
        return "不适用的参数：" + ", ".join(sorted(unknown))
    if error.validator == "anyOf":
        names = []
        for branch in error.validator_value or []:
            required = branch.get("required") if isinstance(branch, dict) else None
            if required:
                names.append(required[0])
        if names:
            return "需要其中之一：" + ", ".join(names)
    if error.validator in ("enum", "type", "minLength", "minItems", "minProperties"):
        return error.validator + "=" + json.dumps(error.validator_value, ensure_ascii=False)
    return error.validator + " 约束未满足"


def parameter_schema(parameter):
    annotation = LIBRARY_INPUTS.get(parameter.name, parameter.annotation)
    if parameter.name == "storage_state" and parameter.annotation is dict:
        annotation = StorageState
    if annotation is inspect.Parameter.empty:
        annotation = object
    schema = compact_annotations(TypeAdapter(annotation).json_schema())
    if parameter.default is None:
        if isinstance(schema.get("type"), str):
            schema["type"] = [schema["type"], "null"]
        else:
            schema = {"anyOf": [schema, {"type": "null"}]}
    elif parameter.default is not inspect.Parameter.empty:
        schema["default"] = parameter.default
    return schema


def request_schema(signature, required=(), nonempty=(), fields=None):
    properties = {
        name: parameter_schema(p) for name, p in signature.parameters.items() if name != "detail"
    }
    definitions = {}
    for schema in properties.values():
        definitions.update(schema.pop("$defs", {}))
    mandatory = {
        name for name, p in signature.parameters.items() if p.default is inspect.Parameter.empty
    }
    mandatory.update(required)
    for name, override in (fields or {}).items():
        if "$ref" in override or "anyOf" in override:
            properties[name] = copy.deepcopy(override)
        else:
            properties[name].update(override)
    for name in mandatory:
        prop = properties[name]
        if isinstance(prop.get("type"), list):
            prop["type"] = [t for t in prop["type"] if t != "null"]
            if len(prop["type"]) == 1:
                prop["type"] = prop["type"][0]
    for name in nonempty:
        prop = properties[name]
        kind = prop.get("type")
        prop[{"string": "minLength", "object": "minProperties", "array": "minItems"}[kind]] = 1
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if definitions:
        result["$defs"] = definitions
    if mandatory:
        result["required"] = sorted(mandatory)
    return result


def factor(operations):
    """Types once; branches contain only accepted fields and actual differences."""
    properties = {}
    definitions = {}
    for operation in operations:
        definitions.update(operation.input_schema.get("$defs", {}))
        for name, schema in operation.input_schema["properties"].items():
            properties.setdefault(name, {})[key(schema)] = schema
    merged = {}
    for name, alternatives in properties.items():
        if len(alternatives) == 1:
            merged[name] = copy.deepcopy(next(iter(alternatives.values())))
        else:
            types = {
                key({k: v for k, v in s.items() if k != "default"}): {
                    k: v for k, v in s.items() if k != "default"
                }
                for s in alternatives.values()
            }
            merged[name] = (
                copy.deepcopy(next(iter(types.values())))
                if len(types) == 1
                else {"anyOf": list(types.values())}
            )
    merged["detail"] = {"enum": ["compact", "full"], "default": "compact"}
    result = {"type": "object", "properties": merged, "additionalProperties": False}
    if operations[0].op is not None:
        merged["op"] = {"enum": [operation.op for operation in operations]}
        result["required"] = ["op"]
        # 不在顶层放 anyOf/oneOf/allOf：Anthropic 会直接拒收
        # "input_schema does not support oneOf, allOf, or anyOf at the top level"。
        # 每个 op 的必填与二选一约束仍由 catalog.call 用 operation.validator
        # 逐 op 校验，客户端需要的提示写进工具描述（见 catalog.schemas）。
    elif "required" in operations[0].input_schema:
        result["required"] = operations[0].input_schema["required"]
    if any("#/$defs/Target" in key(op.input_schema) for op in operations):
        definitions["Target"] = targets.schema()
    if definitions:
        result["$defs"] = definitions
    return result
