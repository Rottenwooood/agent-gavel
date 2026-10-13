"""反检测回归：启动时必须不暴露显而易见的自动化特征。

背景：Playwright 默认让 `navigator.webdriver = true`，页面第一眼即可判定自动化。
我们在启动参数里关掉它，并把无头 UA 里的 'HeadlessChrome' 去掉。
"""


async def test_navigator_webdriver_off(gavel_tools):
    r = await gavel_tools["session_create"]()
    rd = await gavel_tools["page_read"](page_id=r["page_id"], page_features={
        "wd": "navigator.webdriver",
        "headless_ua": "/HeadlessChrome/.test(navigator.userAgent)",
        "has_chrome": "!!window.chrome"})
    assert rd["features"]["wd"] in (False, None), rd  # 不得为 True
    assert rd["features"]["headless_ua"] is False, rd
    assert rd["features"]["has_chrome"] is True, rd
