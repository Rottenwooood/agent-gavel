"""新 runtime 的 MCP 工具聚合入口。main.py 只调 register_runtime_tools(mcp)。"""


def register_runtime_tools(mcp):
    from .action_tools import register_action_tools
    from .artifact_tools import register_artifact_tools
    from .assertion_tools import register_assertion_tools
    from .browser_tools import register_browser_tools
    from .page_tools import register_page_tools

    register_browser_tools(mcp)
    register_page_tools(mcp)
    register_action_tools(mcp)
    register_assertion_tools(mcp)
    register_artifact_tools(mcp)
    # M4+ 追加：workflow_tools
