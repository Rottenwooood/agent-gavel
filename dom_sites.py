"""DOM 站点模板：每个站点(百度/Bing/Google)的操作定义。

与 catalog_data.py（桌面 AT-SPI 操作）并列。这里定义"网页操作"：
navigate(打开站) / type_search(输入) / submit(回车跳转)，
每个动作声明 CSS 选择器和"动作后应验证的页面特征"。

页面特征用 JS 表达式提取（返回标量），expected 描述期望。
"""

DOM_SITES = {
    "baidu": {
        "home": "https://www.baidu.com",
        "search_input": "#kw",
        "search_button": "#su",
        # 动作后应验证的特征（JS 表达式 → 标量）
        "features": {
            "title": "document.title",
            "url": "location.href",
            "input_value": "document.querySelector('#kw') ? document.querySelector('#kw').value : ''",
            "has_result": "!!document.querySelector('.result, #content_left, .c-container')",
        },
    },
    "bing": {
        "home": "https://www.bing.com",
        "search_input": "#sb_form_q",
        "search_button": "#sb_form_go",
        "features": {
            "title": "document.title",
            "url": "location.href",
            "input_value": "document.querySelector('#sb_form_q') ? document.querySelector('#sb_form_q').value : ''",
            "has_result": "!!document.querySelector('#b_results, .b_results')",
        },
    },
    "google": {
        "home": "https://www.google.com",
        "search_input": "textarea[name='q'], input[name='q']",
        "search_button": "input[name='btnK'], button[aria-label*='Google 搜索'], button[aria-label*='Google Search']",
        "features": {
            "title": "document.title",
            "url": "location.href",
            "input_value": "(document.querySelector('textarea[name=q], input[name=q]')||{}).value || ''",
            "has_result": "!!document.querySelector('#search, #rso')",
        },
    },
}


def resolve_dom_op(site, operation, params=None):
    """返回 DOM 站点操作执行所需的数据；无则 None。

    operation: navigate | search
    """
    params = params or {}
    cfg = DOM_SITES.get(site)
    if not cfg:
        return None
    query = params.get("QUERY", "opencode")

    if operation == "navigate":
        return {
            "action": "navigate",
            "selectors": {"url": cfg["home"]},
            "page_features": {"title": "document.title",
                              "url": "location.href"},
            "expected_feature": {
                "title": {"op": "exists"},
            },
            "log_prefix": f"dom_{site}_navigate",
        }

    if operation == "type_search":
        return {
            "action": "set_value",
            "selectors": {"input": cfg["search_input"],
                          "value": query},
            "page_features": {"input_value": cfg["features"]["input_value"]},
            "expected_feature": {
                "input_value": {"op": "eq", "value": query},
            },
            "log_prefix": f"dom_{site}_type",
        }

    if operation == "submit_search":
        # 用点击真实搜索按钮，不用合成 Enter（合成键盘事件 isTrusted=false，
        # 百度等站点不响应，导致误跳热搜词）
        btn = cfg.get("search_button")
        return {
            "action": "click",
            "selectors": {"target": btn},
            "page_features": {
                "title": cfg["features"]["title"],
                "url": cfg["features"]["url"],
                "has_result": cfg["features"]["has_result"],
            },
            "expected_feature": {
                "has_result": {"op": "exists"},
            },
            "log_prefix": f"dom_{site}_submit",
        }

    return None
