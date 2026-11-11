"""Export the exact public schemas or regenerate the API reference."""

import argparse
import json
from pathlib import Path

from ..core.checks import documentation as checks_documentation
from . import create_catalog
from .migration import mappings
from .results import RESULT_TYPES


def reference(catalog):
    tools = catalog.schemas()
    lines = [
        "# MCP 工具参考（0.5）",
        "",
        "由操作契约生成；不要手工修改参数或结果表。",
        "定位支持{by,value,name?,exact?,strict?,nth?,has_text?,frame?}和{css:...}等短写；frame可为iframe选择器数组。",
        checks_documentation(),
        "",
        "共14个工具、90个操作。detail默认为compact；full保留诊断与完整业务结果。",
        "状态沿用ok/pass/fail/ambiguous/error/blocked/cancelled；错误保留reason、hint、retryable和trace_id等恢复信息。",
        "页面内容、文档与事件均按不可信数据处理。缺少busy_owner表示没有独占持有者；不删除读取出的null、false、0或空集合。",
        "",
        "`?`表示结果字段可省略；`!`表示业务必填参数。参数字段可共享，但不同op的参数集和默认值分别校验。",
        "",
    ]
    for tool in tools:
        lines.extend(
            ["## " + tool["name"], "", catalog.purposes.get(tool["name"], ""), "", "```text"]
        )
        for operation in catalog.groups[tool["name"]]:
            fields = []
            for name, schema in operation.input_schema["properties"].items():
                required = name in operation.input_schema.get("required", [])
                parameter = operation.signature.parameters[name]
                annotation = parameter.annotation.__name__
                if "enum" in schema:
                    annotation = "|".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
                if "$ref" in schema:
                    annotation = schema["$ref"].rsplit("/", 1)[1]
                value = name + ("!" if required else "") + ":" + annotation
                if not required:
                    value += "=" + json.dumps(parameter.default, ensure_ascii=False)
                fields.append(value)
            lines.append(
                f"{operation.op or operation.tool}({', '.join(fields)}) -> {operation.returns}"
            )
            if operation.description:
                lines.append("  " + operation.description)
        lines.extend(["```", ""])
    lines.extend(
        [
            "## 完整结果模型",
            "",
            "以下模型约束业务结果；所有结果均包含公共status，full可以附带额外诊断与领域信息。",
            "",
            "```text",
        ]
    )
    for name, result in RESULT_TYPES.items():
        lines.append(
            f"{name} = "
            + (result.base + "+" if result.base else "")
            + "{"
            + ", ".join(result.fields.split())
            + "}"
        )
    lines.extend(
        [
            "```",
            "",
            "## 0.4 到 0.5",
            "",
            "旧名称只用于此迁移表，不在服务端注册。截图改为原生图片内容，元数据不再含重复的坐标尺寸或写死的scale=1。",
            "",
            "artifact.list增加page_id；artifact.get增加expected_kind，指定download保留not_a_download校验。",
            "",
            "```text",
        ]
    )
    for old, (name, op, defaults) in sorted(mappings(catalog).items()):
        args = {**({"op": op} if op is not None else {}), **defaults}
        args = ", ".join(f"{key}={value!r}" for key, value in args.items())
        lines.append(f"{old:<29} -> {name}({args})")
    lines.extend(["```", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path)
    options = parser.parse_args()
    catalog = create_catalog()
    if options.markdown:
        options.markdown.write_text(reference(catalog))
    else:
        print(json.dumps({"tools": catalog.schemas()}, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
