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
    # ---- 微信（Linux 4.x，RadiumWMPF/Chromium 混编）----
    # 实测：AT-SPI 暴露原生导航/会话/消息控件；输入框 supports_editable_text。
    # type 中文必须走 clipboard（xdotool 无中文 keysym），靠 window_id 定位
    # （带 app_id 的按键会被 computer-use-linux 的 AT-SPI 焦点校验拦截）。
    # 语义点击（role+name）对唯一名元素可靠，不依赖窗口坐标。
    {
        "app": "wechat",
        "operation": "activate",
        "desc": "激活微信主窗口到前台",
        "target": {"app_id": "wechat.desktop"},
        "action": "activate_window",
        "action_args": {},
        "assertions": [
            {"type": "element_appears", "role": "frame", "name": "微信"}
        ],
        "timeout_s": 6.0,
    },
    {
        "app": "wechat",
        "operation": "open_contacts",
        "desc": "切到通讯录页",
        "target": {"app_id": "wechat.desktop"},
        "action": "click",
        "action_args": {"role": "push button", "name": "通讯录"},
        "assertions": [
            {"type": "element_appears", "role": "list", "name": "通讯录"}
        ],
        "timeout_s": 6.0,
    },
    {
        "app": "wechat",
        "operation": "send_message_to_file",
        "desc": "给文件传输助手发一条消息（聚焦输入→type→点发送）",
        "target": {"app_id": "wechat.desktop"},
        "flow": [
            {
                "action": "click",
                "action_args": {"role": "text", "name": "文件传输助手"},
                "timeout_s": 5.0,
            },
            {
                "action": "type",
                "action_args": {"text": "$MSG", "method": "clipboard"},
                "timeout_s": 6.0,
            },
            {
                "action": "click",
                "action_args": {"role": "push button", "name": "发送(S)"},
                "assertions": [
                    {"type": "element_appears", "role": "list item",
                     "name": "$MSG"}
                ],
                "timeout_s": 8.0,
            },
        ],
        "timeout_s": 20.0,
    },
    {
        "app": "wechat",
        "operation": "delete_last_message",
        "desc": "删除消息区刚发的 $MSG。坐标右键针对微信全屏(0,32)布局下消息气泡"
               "右缘(x≈1700,y 随行)；语义右键(list item)会点到行中心空白仅 SetFocus，"
               "弹不出菜单，故必须坐标右键气泡。前置把窗口归位到 (0,32)。",
        "target": {"app_id": "wechat.desktop"},
        "flow": [
            {
                "action": "move_window",
                "action_args": {"x": 0, "y": 32},
                "timeout_s": 5.0,
            },
            {
                "action": "click",
                "action_args": {"button": "right", "x": 1700, "y": 710},
                "timeout_s": 6.0,
            },
            {
                "action": "click",
                "action_args": {"role": "menu item", "name": "删除"},
                "timeout_s": 6.0,
            },
            {
                "action": "click",
                "action_args": {"role": "push button", "name": "删除"},
                "assertions": [
                    {"type": "element_disappears", "role": "list item",
                     "name": "$MSG"}
                ],
                "timeout_s": 8.0,
            },
        ],
        "timeout_s": 30.0,
    },
]


def resolve_operation(app, operation, params=None):
    """按 app+operation 查目录，返回操作定义；不存在返回 None。

    params 用于替换 $QUERY 这类占位符（递归：action_args/assertions/flow 内）。
    """
    params = params or {}
    for op in OPERATION_CATALOG:
        if op["app"] == app and op["operation"] == operation:
            import copy
            resolved = copy.deepcopy(op)

            def rec(x):
                if isinstance(x, dict):
                    return {k: rec(v) for k, v in x.items()}
                if isinstance(x, list):
                    return [rec(v) for v in x]
                if isinstance(x, str):
                    out = x
                    for ph, pv in params.items():
                        out = out.replace(f"${ph}", str(pv))
                    return out
                return x

            # 保留顶层非动作字段（app/operation/desc/flow），递归替换其余
            resolved = rec(resolved)
            return resolved
    return None
