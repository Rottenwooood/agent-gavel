"""新 runtime 的 MCP 工具聚合入口。main.py 只调 register_runtime_tools(mcp)。"""


def register_runtime_tools(mcp):
    from .browser_tools import register_browser_tools
    register_browser_tools(mcp)
    # M2+ 追加：action_tools / assertion_tools / artifact_tools / workflow_tools
