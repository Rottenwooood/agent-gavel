"""M2：expect / page_wait_for_* MCP 工具（docs/tech-plan.md §8、§10）。

expect 做 UI/业务层断言，三态：pass / fail / ambiguous（有动作前后签名时）。
"""

from ..core.checks import OPS, READ_KINDS
from ..core.errors import GavelError
from .common import backend, ids


def register_assertion_tools(catalog):
    @catalog.operation(
        returns="Assertions", tool="assertion", op="check", one_nonempty=("checks", "assertions")
    )
    async def expect(
        page_id: str = None, checks: dict = None, assertions: dict = None, wait_s: float = 6.0
    ):
        "checks/assertions择一非空；同时给以checks优先；按wait_s轮询。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        checks = checks or assertions
        if not checks:
            raise GavelError("bad_args", "需要 checks（或 assertions）")
        be = backend()
        ev = await be.evaluate_checks(handle, checks, wait_s=wait_s)
        return {"status": ev["status"], **ids(handle), "assertions": ev["assertions"]}

    @catalog.operation(returns="AssertionSchema")
    async def assertion_schema():
        "断言完整格式与示例。"
        return {
            "status": "ok",
            "forms": {
                "map": "{名字: {op, value?, expected?, read?, selector?, attr?, expr?, frame?}}",
                "flat": "{url_contains: 'x', text_eq: 'y', title_contains: 'z', count_gte: 1}",
                "top_op": "{title: '百度', op: 'contains'}",
            },
            "read": sorted(READ_KINDS),
            "ops": sorted(OPS),
            "value_alias": "value 与 expected 等价",
            "defaults": "read 缺省按名字（url/title/...）或 selector（元素文本）；判断不出会报错",
            "tri_state": {
                "pass": "满足",
                "fail": "未满足",
                "ambiguous": "断言没过但页面变了（拿不准，附 diff）",
            },
            "examples": [
                {"u": {"read": "url", "op": "contains", "value": "baidu.com"}},
                {"t": {"read": "title", "op": "contains", "value": "百度"}},
                {"url_contains": "baidu.com"},
                {"n": {"read": "count", "selector": ".result", "op": "count_gte", "value": 1}},
            ],
            "note": "文本类返回（page_text/page_read 等）标 untrusted=true，注入模型时按不可信内容处理",
        }

    @catalog.operation(returns="Location", required=("url",), nonempty=("url",))
    async def page_wait_for_url(page_id: str = None, url: str = None, timeout_s: float = 30.0):
        "精确URL或glob匹配。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        if not url:
            raise GavelError("bad_args", "需要 url")
        be = backend()
        res = await handle.actor.submit(
            "wait_for_url",
            lambda: be.wait_for_url(handle, url, timeout_s=timeout_s),
            timeout_s=timeout_s + 5,
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(
        returns="WaitSelector",
        required=("selector",),
        nonempty=("selector",),
        fields={"state": {"enum": ["attached", "detached", "visible", "hidden"]}},
    )
    async def page_wait_for_selector(
        page_id: str = None, selector: str = None, state: str = "visible", timeout_s: float = 30.0
    ):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        if not selector:
            raise GavelError("bad_args", "需要 selector")
        be = backend()
        res = await handle.actor.submit(
            "wait_for_selector",
            lambda: be.wait_for_selector(handle, selector, state=state, timeout_s=timeout_s),
            timeout_s=timeout_s + 5,
        )
        if rt.recorder is not None and rt.recorder.active:
            rt.recorder.record(
                handle,
                {
                    "type": "wait_for_selector",
                    "selector": selector,
                    "state": state,
                    "timeout_s": timeout_s,
                },
            )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(
        returns="WaitText",
        required=("text",),
        fields={"state": {"enum": ["attached", "detached", "visible", "hidden"]}},
    )
    async def page_wait_for_text(
        page_id: str = None,
        text: str = None,
        selector: str = None,
        state: str = "visible",
        timeout_s: float = 30.0,
    ):
        "等待首个文本命中；selector限定范围，不要求唯一。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "wait_for_text",
            lambda: be.wait_for_text(
                handle, text, state=state, timeout_s=timeout_s, selector=selector
            ),
            timeout_s=timeout_s + 5,
        )
        if rt.recorder is not None and rt.recorder.active:
            step = {"type": "wait_for_text", "text": text, "timeout_s": timeout_s}
            if selector:
                step["selector"] = selector
            rt.recorder.record(handle, step)
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(
        returns="WaitLoad", fields={"state": {"enum": ["load", "domcontentloaded", "networkidle"]}}
    )
    async def page_wait_for_load(page_id: str = None, state: str = "load", timeout_s: float = 30.0):
        """"""
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "wait_for_load",
            lambda: be.wait_load_state(handle, state, timeout_s),
            timeout_s=timeout_s + 5,
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(returns="WaitResponse")
    async def page_wait_for_response(
        page_id: str = None, url: str = None, status: int = None, timeout_s: float = 30.0
    ):
        "按URL子串/glob和HTTP状态码筛选响应。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "wait_for_response",
            lambda: be.wait_for_response(handle, url=url, status=status, timeout_s=timeout_s),
            timeout_s=timeout_s + 5,
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(returns="Download")
    async def page_wait_for_download(page_id: str = None, timeout_s: float = 30.0):
        "等待下载完成并返回 artifact（由 page.on('download') 自动捕获）。"
        from ..core.errors import GavelError
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        ev = await rt.events.wait_for(
            lambda e: e.get("event") == "download_completed" and e.get("page_id") == handle.page_id,
            timeout_s,
        )
        if ev is None:
            raise GavelError("timeout", "等待下载超时", retryable=True)
        info = rt.artifacts.get(ev["artifact_id"])
        return {
            "status": "ok",
            **ids(handle),
            "artifact_id": ev["artifact_id"],
            "suggested_filename": info["suggested_filename"],
            "size_bytes": info["size_bytes"],
            "sha256": info["sha256"],
            "mime_type": info["mime_type"],
        }

    @catalog.operation(returns="Popup")
    async def page_wait_for_popup(page_id: str = None, url: str = None, timeout_s: float = 30.0):
        "等待由该 page 打开的新页面（popup / target=_blank）。"
        from ..core.errors import GavelError
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)

        def pred(e):
            if e.get("event") != "page_created":
                return False
            if e.get("opener_page_id") != handle.page_id:
                return False
            return not (url and url not in (e.get("url") or ""))

        ev = await rt.events.wait_for(pred, timeout_s)
        if ev is None:
            raise GavelError("timeout", "等待 popup 超时", retryable=True)
        new_page = rt.pages.get(ev["page_id"])
        return {
            "status": "ok",
            **ids(handle),
            "new_page_id": ev["page_id"],
            "opener_page_id": ev.get("opener_page_id"),
            "url": ev.get("url"),
            "new_page": new_page.to_dict() if new_page else None,
        }

    @catalog.operation(returns="Event")
    async def page_wait_for_event(
        kinds: list = None, page_id: str = None, timeout_s: float = 30.0, include_past: bool = False
    ):
        "默认只等调用后的新事件；include_past才接受缓冲历史。"
        import time

        from ..core.errors import GavelError
        from ..runtime import get_runtime

        rt = get_runtime()
        allowed = set(kinds) if kinds else None

        def pred(e):
            if allowed is not None and e.get("event") not in allowed:
                return False
            return not (page_id and e.get("page_id") not in (page_id, None))

        since = None if include_past else time.time()
        ev = await rt.events.wait_for(pred, timeout_s, since=since)
        if ev is None:
            raise GavelError("timeout", "等待事件超时", retryable=True)
        return {"status": "ok", "event": ev}
