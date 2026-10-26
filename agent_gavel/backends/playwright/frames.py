"""iframe 作用域解析。"""


def frame_root(page, frame):
    """frame 可为选择器或选择器列表（嵌套 iframe）。"""
    if not frame:
        return page
    root = page
    for item in (frame if isinstance(frame, list) else [frame]):
        root = root.frame_locator(item)
    return root
