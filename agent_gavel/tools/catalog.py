"""Single operation catalog: schemas, dispatch and response contracts.

This layer selects a handler and checks its public contract. Domain behavior
stays in the handler's existing owner; it is never reimplemented by a dispatcher.
"""

import inspect
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from ..core.actions import documentation as action_documentation
from ..core.actions import spec
from ..core.checks import documentation as checks_documentation
from ..core.errors import UNKNOWN_OPERATION, UNKNOWN_TOOL, GavelError, wrap_exception
from ..core.results import ToolReply
from .common import envelope
from .results import RESULT_TYPES, project
from .schemas import error_hint, factor, request_schema

SINGLE_TOOLS = frozenset({"console_get", "performance_metrics", "daemon_shutdown", "screenshot"})


@dataclass
class Operation:
    tool: str
    op: str | None
    handler: object
    signature: inspect.Signature
    input_schema: dict
    returns: str
    description: str
    validator: object = None


class Catalog:
    def __init__(self):
        self.operations = {}
        self.groups = defaultdict(list)
        self.purposes = {}

    def operation(
        self,
        *,
        returns,
        tool=None,
        op=None,
        required=(),
        nonempty=(),
        fields=None,
        action=None,
        one_nonempty=(),
    ):
        def register(handler):
            name = handler.__name__
            family, action_name = (
                (name, None) if name in SINGLE_TOOLS or "_" not in name else name.split("_", 1)
            )
            family = tool or family
            action_name = op if op is not None else action_name
            signature = inspect.signature(handler)
            own_fields = dict(fields or {})
            own_required, own_nonempty = set(required), set(nonempty)
            if action is not None:
                defaults = {
                    n: p.default
                    for n, p in signature.parameters.items()
                    if p.default is not inspect.Parameter.empty
                }
                contract = spec(action).tool_request(defaults)
                own_fields.update(contract["fields"])
                own_required.update(contract["required"])
                own_nonempty.update(contract["nonempty"])
            schema = request_schema(signature, own_required, own_nonempty, own_fields)
            if one_nonempty:
                schema["anyOf"] = [
                    {
                        "required": [field_name],
                        "properties": {field_name: {"type": "object", "minProperties": 1}},
                    }
                    for field_name in one_nonempty
                ]
            operation = Operation(
                family,
                action_name,
                handler,
                signature,
                schema,
                returns,
                inspect.getdoc(handler) or "",
            )
            if (family, action_name) in self.operations:
                raise ValueError(f"duplicate operation: {family}.{action_name}")
            if returns not in RESULT_TYPES:
                raise ValueError(f"unknown result contract: {returns}")
            self.operations[family, action_name] = operation
            self.groups[family].append(operation)
            return handler

        return register

    def schemas(self):
        tools = []
        for name, operations in self.groups.items():
            schema = factor(operations)
            for operation in operations:
                own = dict(operation.input_schema)
                if "$defs" in schema:
                    own["$defs"] = schema["$defs"]
                operation.validator = Draft202012Validator(own)
            description_groups = defaultdict(list)
            for operation in operations:
                label = operation.op or name
                description_groups[operation.returns, operation.description].append(label)
            descriptions = []
            for (result_name, note), labels in description_groups.items():
                suffix = f": {note}" if note else ""
                descriptions.append(f"{'|'.join(labels)}→{result_name}{suffix}")
            result_names = list(dict.fromkeys(operation.returns for operation in operations))
            results = "; ".join(f"{n}={RESULT_TYPES[n].shape}" for n in result_names)
            description = (
                self.purposes.get(name, "") + "\n" + "\n".join(descriptions) + "\n" + results
            )
            if (
                any(
                    {"checks", "assertions"} & set(operation.input_schema["properties"])
                    for operation in operations
                )
                or name == "workflow"
            ):
                description += "\n" + checks_documentation()
            if name == "workflow":
                description += "\n" + action_documentation()
            description += "\nfull含诊断字段与细节。"
            tools.append({"name": name, "description": description.strip(), "inputSchema": schema})
        return tools

    async def call(self, name, arguments):
        started = time.perf_counter()
        operation = None
        detail = "compact"
        try:
            if not isinstance(name, str):
                raise GavelError("bad_args", "工具名必须是字符串")
            if name not in self.groups:
                raise GavelError(UNKNOWN_TOOL, f"没有这个工具：{name}")
            if not isinstance(arguments, dict):
                raise GavelError("bad_args", "arguments 必须是对象")
            arguments = dict(arguments)
            has_op = "op" in arguments
            op = arguments.pop("op", None)
            detail = arguments.pop("detail", "compact")
            if detail not in ("compact", "full"):
                raise GavelError("bad_args", "detail 取 compact|full")
            if name in SINGLE_TOOLS:
                if has_op:
                    raise GavelError("bad_args", "单操作工具不接受op参数")
            elif not isinstance(op, str):
                raise GavelError("bad_args", "需要字符串op参数")
            operation = self.operations.get((name, op))
            if operation is None:
                raise GavelError(UNKNOWN_OPERATION, f"未知操作：{name}.{op}")
            if operation.validator is None:
                self.schemas()
            error = next(operation.validator.iter_errors(arguments), None)
            if error is not None:
                path = ".".join(str(p) for p in error.absolute_path)
                raise GavelError(
                    "bad_args",
                    f"参数不符合 {name}.{op} 契约：{path or 'arguments'}",
                    hint=error_hint(error),
                )
            if "detail" in operation.signature.parameters:
                arguments["detail"] = detail
            value = await operation.handler(**arguments)
            reply = value if isinstance(value, ToolReply) else ToolReply(value)
            RESULT_TYPES[operation.returns].validate(reply.result)
        except Exception as exc:
            error = exc if isinstance(exc, GavelError) else wrap_exception(exc)
            reply = ToolReply(error.to_dict())
        reply.result = envelope(
            reply.result,
            operation_id="op_" + uuid.uuid4().hex[:8],
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        reply.result = project(operation.returns if operation else None, reply.result, detail)
        return reply
