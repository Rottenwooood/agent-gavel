"""M2：导航/读取/快照/探索/截图/PDF MCP 工具（docs/tech-plan.md §8）。"""

import asyncio

from ..core.errors import GavelError
from .common import backend, ids, rec, run_with_optional_assert


def register_page_tools(catalog):
    @catalog.operation(
        returns="Navigation",
        action="navigate",
        fields={"wait_until": {"enum": ["load", "domcontentloaded", "networkidle", "commit"]}},
    )
    async def page_navigate(
        page_id: str = None,
        url: str = None,
        wait_until: str = "domcontentloaded",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
        fail_on_http_error: bool = False,
        min_text_chars: int = None,
    ):
        "HTTP错误可设失败；min_text_chars防空页。"
        from ..runtime import get_runtime

        rt = get_runtime()
        if not url:
            raise GavelError("bad_args", "需要 url")
        handle = rt.page_mgr.resolve(page_id)
        be = backend()

        async def post(out, execution, handle):
            code = execution.get("status_code")
            if isinstance(code, int) and code >= 400:
                out["http_error"] = True
                out["http_status"] = code
                out["warning"] = f"http_{code}"
                if fail_on_http_error:
                    out["status"] = "error"
                    out["reason"] = "http_error"
            try:
                tl = await handle.actor.submit(
                    "content_len",
                    lambda: handle.page.evaluate(
                        "() => document.body ? document.body.innerText.length : 0"
                    ),
                )
            except Exception:
                tl = None
            if isinstance(tl, int):
                out["text_len"] = tl
                if min_text_chars is not None and tl < min_text_chars:
                    out["status"] = "fail"
                    out["reason"] = "empty_page"
                elif min_text_chars is None and tl < 20:
                    prev = out.get("warning")
                    out["warning"] = (
                        f"{prev} page_looks_empty".strip() if prev else "page_looks_empty"
                    )

        return await run_with_optional_assert(
            rt,
            handle,
            "navigate",
            lambda: be.goto(handle, url, wait_until=wait_until, timeout_s=timeout_s),
            assertions=assertions,
            wait_s=wait_s,
            timeout_s=timeout_s,
            record=rec("navigate", None, url=url),
            flatten=True,
            post=post,
        )

    @catalog.operation(
        returns="Snapshot",
        fields={"level": {"enum": ["summary", "compact", "interactive", "full"]}},
    )
    async def page_snapshot(page_id: str = None, level: str = "summary", limit: int = 50):
        "summary摘要/compact锚点/interactive元素/full再含HTML。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "snapshot",
            lambda: be.retry_on_nav_race(
                handle, lambda: be.snapshot(handle, level=level, limit=limit)
            ),
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(
        returns="Text", fields={"mode": {"enum": ["text", "content", "html", "links"]}}
    )
    async def page_text(
        page_id: str = None, selector: str = None, mode: str = "text", max_chars: int = 50000
    ):
        "text=innerText/content=textContent；links整页且忽略selector/max_chars。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_text",
            lambda: be.retry_on_nav_race(
                handle, lambda: be.text(handle, selector=selector, mode=mode)
            ),
        )
        if mode != "links" and isinstance(res.get("text"), str) and len(res["text"]) > max_chars:
            res["text"] = res["text"][:max_chars]
            res["truncated"] = True
        return {"status": "ok", **ids(handle), "untrusted": True, **res}

    @catalog.operation(returns="Links")
    async def page_links(page_id: str = None, max_links: int = 500):
        "链接items与裁剪前total；max_links默认500。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit("page_links", lambda: be.text(handle, mode="links"))
        links = res.get("links", [])[:max_links]
        return {"status": "ok", **ids(handle), "items": links, "total": len(res.get("links", []))}

    @catalog.operation(returns="Read", required=("page_features",), nonempty=("page_features",))
    async def page_read(page_id: str = None, page_features: dict = None, selector: str = None):
        "page_features为JS表达式映射；selector先等5秒。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        features = page_features or {}
        if not features:
            raise GavelError("bad_args", "需要 page_features")
        if selector:
            try:
                await handle.page.wait_for_selector(selector, timeout=5000)
            except Exception:
                return {
                    "status": "fail",
                    "reason": "selector_not_found",
                    **ids(handle),
                    "selector": selector,
                    "hint": "selector 未在 5s 内出现",
                }
        res = await handle.actor.submit(
            "page_read",
            lambda: be.retry_on_nav_race(handle, lambda: be.read(handle, features)),
        )
        out = {"status": "ok", **ids(handle), "untrusted": True, "features": res["features"]}
        if res["errors"]:
            out["errors"] = res["errors"]
            if res["features"]:
                out["complete"] = False
            else:
                out["status"] = "error"
        empty = [k for k, v in res["features"].items() if v in (None, "", [])]
        if empty:
            out["empty"] = empty
        return out

    @catalog.operation(returns="Explore")
    async def page_explore(
        page_id: str = None,
        tag: str = None,
        text_contains: str = None,
        head: int = None,
        tail: int = None,
        include_all: bool = False,
        include_hidden: bool = False,
    ):
        "head/tail裁剪；include_all含无锚点项；候选唯一性可未知。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_explore",
            lambda: be.retry_on_nav_race(
                handle,
                lambda: be.explore(
                    handle,
                    tag=tag,
                    text_contains=text_contains,
                    head=head,
                    tail=tail,
                    include_all=include_all,
                    include_hidden=include_hidden,
                ),
            ),
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(returns="Explore", required=("text",), nonempty=("text",))
    async def page_find_text(
        page_id: str = None, text: str = None, limit: int = 10, include_hidden: bool = False
    ):
        "按文本找最近可点击祖先。"
        from ..runtime import get_runtime

        rt = get_runtime()
        if not text:
            raise GavelError("bad_args", "需要 text")
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_find_text",
            lambda: be.retry_on_nav_race(
                handle,
                lambda: be.find_text(handle, text, limit=limit, include_hidden=include_hidden),
            ),
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(returns="Meta")
    async def page_meta(page_id: str = None):
        "固定元信息提取，含canonical/og/h1/h2。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_meta",
            lambda: be.retry_on_nav_race(handle, lambda: be.page_meta(handle)),
        )
        return {"status": "ok", **ids(handle), "untrusted": True, **res}

    @catalog.operation(returns="ArtifactRef")
    async def page_pdf(page_id: str = None, save_to: str = None):
        "PDF产物；save_to为导出目标目录。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit("page_pdf", lambda: be.pdf(handle))
        info = await asyncio.to_thread(
            rt.artifacts.save_bytes,
            res["data"],
            filename=f"{handle.page_id}.pdf",
            kind="pdf",
            mime_type="application/pdf",
            source_url=handle.page.url,
            session_id=handle.context.lease_session_id(),
            page_id=handle.page_id,
        )
        out = {
            "status": "ok",
            **ids(handle),
            "artifact_id": info["artifact_id"],
            "size_bytes": info["size_bytes"],
        }
        if save_to:
            exp = await asyncio.to_thread(
                rt.artifacts.export, info["artifact_id"], save_to, policies=rt.policies
            )
            out["exported"] = True
            out["exported_filename"] = exp["filename"]
        return out
