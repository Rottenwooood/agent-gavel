"""Notation bridge for pre-0.5 behavioral tests, not a public MCP server."""

from agent_gavel.tools import create_catalog
from agent_gavel.tools.migration import mappings


def register_legacy_tools(collector):
    catalog = create_catalog()
    catalog.schemas()
    for old_name, (name, op, defaults) in mappings(catalog).items():

        def bind(tool, operation, initial):
            async def invoke(**arguments):
                payload = {"detail": "full", **initial, **arguments}
                if operation is not None:
                    payload["op"] = operation
                return (await catalog.call(tool, payload)).result

            return invoke

        call = bind(name, op, defaults)
        call.__name__ = old_name
        collector.tool()(call)
