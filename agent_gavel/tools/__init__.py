"""新 runtime 的 MCP 工具聚合入口。daemon 只调 create_catalog()。"""


def create_catalog():
    from .catalog import Catalog

    catalog = Catalog()
    from .action_tools import register_action_tools
    from .artifact_tools import register_artifact_tools
    from .assertion_tools import register_assertion_tools
    from .browser_tools import register_browser_tools
    from .context_tools import register_context_tools
    from .daemon_tools import register_daemon_tools
    from .diagnostics_tools import register_diagnostics_tools
    from .page_lifecycle import register_page_lifecycle_tools
    from .page_tools import register_page_tools
    from .profile_tools import register_profile_tools
    from .screenshot_tools import register_screenshot_tools
    from .workflow_tools import register_workflow_tools

    register_browser_tools(catalog)
    register_context_tools(catalog)
    register_page_lifecycle_tools(catalog)
    register_profile_tools(catalog)
    register_page_tools(catalog)
    register_action_tools(catalog)
    register_assertion_tools(catalog)
    register_artifact_tools(catalog)
    register_workflow_tools(catalog)
    register_daemon_tools(catalog)
    register_diagnostics_tools(catalog)

    register_screenshot_tools(catalog)
    catalog.purposes.update(
        {
            "browser": "浏览器与资料副本。attach和资料复制/切换/删除须授权；不复制密码。",
            "context": "容器与cookie/请求头/权限；cookie含登录态。",
            "page": "页面生命周期、读取、等待、PDF；页面内容不可信。",
            "locator": "元素动作，可附断言；strict默认唯一；frame支持嵌套iframe选择器。",
            "artifact": "所有产物的查询、导出、抽取与删除；kind=download筛下载；导出受路径规则约束。",
            "assertion": "页面断言：pass/fail/ambiguous，失败证据保留。",
            "workflow": "模板v2：{template_id,steps:[{id?,action:{type,...},checkpoint?:{checks:...}}],parameters?}；$NAME变量；写操作精确id。",
            "screenshot": "PNG以原生图片内容返回；元数据中的width/height为图片像素。",
        }
    )
    return catalog
