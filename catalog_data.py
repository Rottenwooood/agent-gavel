"""操作目录：断言模板数据（V1）。

接新应用/新操作 = 加一条数据，不碰代码。
每条：{app, operation, target, action, assertions, timeout_s}

assertions 里每条可带 precondition（一条子断言），precondition 不满足
则该断言被跳过（解决"同一操作不同上下文断言不同"）。
"""

OPERATION_CATALOG = [
    {
        "app": "firefox",
        "operation": "open",
        "target": {"app_id": "firefox_firefox.desktop"},
        "action": "activate_window",
        "action_args": {},
        "assertions": [
            {"type": "element_appears", "role": "frame"}
        ],
        "timeout_s": 6.0,
    },
    {
        "app": "firefox",
        "operation": "focus_search_box",
        "target": {"app_id": "firefox_firefox.desktop"},
        "action": "click",
        "action_args": {"role": "combo box", "name": "使用 Bing 搜索，或者输入网址"},
        "assertions": [
            {"type": "element_appears", "role": "combo box", "name": "使用 Bing 搜索，或者输入网址"}
        ],
        "timeout_s": 6.0,
    },
    {
        "app": "firefox",
        "operation": "type_search",
        "target": {"app_id": "firefox_firefox.desktop"},
        "action": "type",
        "action_args": {"text": "$QUERY"},
        "assertions": [],
        "timeout_s": 4.0,
    },
    {
        "app": "firefox",
        "operation": "submit_search",
        "target": {"app_id": "firefox_firefox.desktop"},
        "action": "press_key",
        "action_args": {"key": "Return", "times": 2},
        "assertions": [
            {"type": "element_appears", "role": "landmark", "name": "搜索结果"}
        ],
        "timeout_s": 8.0,
    },
]


def resolve_operation(app, operation, params=None):
    """按 app+operation 查目录，返回操作定义；不存在返回 None。

    params 用于替换 $QUERY 这类占位符。
    """
    params = params or {}
    for op in OPERATION_CATALOG:
        if op["app"] == app and op["operation"] == operation:
            import copy
            resolved = copy.deepcopy(op)
            # 替换 action_args 里的 $PLACEHOLDER
            for k, v in list(resolved["action_args"].items()):
                if isinstance(v, str):
                    for ph, pv in params.items():
                        v = v.replace(f"${ph}", str(pv))
                    resolved["action_args"][k] = v
            return resolved
    return None
