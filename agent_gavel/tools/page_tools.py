"""M2：导航/读取/快照/探索/截图/PDF MCP 工具（docs/tech-plan.md §8）。"""

from ..runtime.errors import GavelError
from .common import backend, guard, ids


def register_page_tools(mcp):
    @mcp.tool()
    @guard
    async def page_navigate(page_id: str = None, url: str = None,
                            wait_until: str = "domcontentloaded",
                            timeout_s: float = 30.0,
                            assertions: dict = None, wait_s: float = 6.0):
        """导航到 URL 并（可选）断言。唯一的导航入口。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        if not url:
            raise GavelError("bad_args", "需要 url")
        rt.policies.check_domain(url)
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        before = await be.page_signature(handle.page) if assertions else None
        url_before = handle.page.url
        res = await handle.actor.submit(
            "navigate",
            lambda: be.goto(handle, url, wait_until=wait_until,
                            timeout_s=timeout_s),
            timeout_s=timeout_s + 5)
        if rt.recorder is not None and rt.recorder.active:
            rt.recorder.record(handle, {"type": "navigate", "url": url},
                               url_before=url_before, url_after=handle.page.url)
        out = {"status": "ok", **ids(handle), **res}
        if assertions:
            ev = await be.evaluate_checks(handle.page, assertions,
                                          before_sig=before, wait_s=wait_s)
            out["status"] = ev["status"]
            out["assertions"] = ev["assertions"]
        return out

    @mcp.tool()
    @guard
    async def page_snapshot(page_id: str = None, level: str = "summary",
                            limit: int = 50):
        """页面摘要（summary/interactive/full）。页面内容标记 untrusted。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "snapshot", lambda: be.snapshot(handle.page, level=level, limit=limit))
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_text(page_id: str = None, selector: str = None,
                        mode: str = "text", max_chars: int = 50000):
        """取页面/元素文本。mode: text|content|html|links。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_text",
            lambda: be.text(handle.page, selector=selector, mode=mode))
        if mode != "links" and isinstance(res.get("text"), str) \
                and len(res["text"]) > max_chars:
            res["text"] = res["text"][:max_chars]
            res["truncated"] = True
        return {"status": "ok", **ids(handle), "untrusted": True, **res}

    @mcp.tool()
    @guard
    async def page_links(page_id: str = None, max_links: int = 500):
        """取页面所有链接 [{text, href}]。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_links", lambda: be.text(handle.page, mode="links"))
        links = res.get("links", [])[:max_links]
        return {"status": "ok", **ids(handle), "items": links,
                "total": len(res.get("links", []))}

    @mcp.tool()
    @guard
    async def page_read(page_id: str = None, page_features: dict = None,
                        selector: str = None):
        """纯读取值（不判定/不重试）：{名字: JS表达式} → 实际值。

        selector 给定时先等它出现。表达式支持函数形式 `"() => { ...; return ...; }"`。
        """
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
                return {"status": "no_match", **ids(handle),
                        "selector": selector,
                        "hint": "selector 未在 5s 内出现"}
        res = await handle.actor.submit("page_read",
                                        lambda: be.read(handle.page, features))
        out = {"status": "ok", **ids(handle), "features": res["features"]}
        if res["errors"]:
            out["errors"] = res["errors"]
            out["status"] = "partial_error" if res["features"] else "error"
        empty = [k for k, v in res["features"].items() if v in (None, "", [])]
        if empty:
            out["empty"] = empty
        return out

    @mcp.tool()
    @guard
    async def page_explore(page_id: str = None, tag: str = None,
                          text_contains: str = None, head: int = None,
                          tail: int = None, include_all: bool = False,
                          include_hidden: bool = False):
        """枚举可交互元素，给验证过唯一的锚点 + 候选 locator。

        裁剪：tag / text_contains / head / tail；include_all 返回无锚点元素。
        """
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_explore",
            lambda: be.explore(handle.page, tag=tag, text_contains=text_contains,
                               head=head, tail=tail, include_all=include_all,
                               include_hidden=include_hidden))
        return {"status": "ok", **ids(handle), **res}

    @mcp.tool()
    @guard
    async def page_screenshot(page_id: str = None, full_page: bool = False,
                              selector: str = None, save_to: str = None):
        """截图 → artifact。返回 artifact_id + 坐标元数据。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "page_screenshot",
            lambda: be.screenshot(handle.page, full_page=full_page,
                                  selector=selector))
        info = rt.artifacts.save_bytes(
            res["data"], filename=f"{handle.page_id}.png", kind="screenshot",
            mime_type="image/png", source_url=handle.page.url,
            session_id=handle.session_id, page_id=handle.page_id)
        out = {"status": "ok", **ids(handle),
               "artifact_id": info["artifact_id"],
               "width": res["width"], "height": res["height"],
               "coordinate_width": res["width"],
               "coordinate_height": res["height"], "scale": 1.0,
               "device_scale_factor": res["device_scale_factor"]}
        if save_to:
            exp = rt.artifacts.export(info["artifact_id"], save_to,
                                      policies=rt.policies)
            out["exported_to"] = exp["exported_to"]
        return out

    @mcp.tool()
    @guard
    async def page_pdf(page_id: str = None, save_to: str = None):
        """生成页面 PDF → artifact（仅无头模式）。"""
        from ..runtime import get_runtime
        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit("page_pdf", lambda: be.pdf(handle))
        info = rt.artifacts.save_bytes(
            res["data"], filename=f"{handle.page_id}.pdf", kind="pdf",
            mime_type="application/pdf", source_url=handle.page.url,
            session_id=handle.session_id, page_id=handle.page_id)
        out = {"status": "ok", **ids(handle),
               "artifact_id": info["artifact_id"],
               "size_bytes": info["size_bytes"]}
        if save_to:
            exp = rt.artifacts.export(info["artifact_id"], save_to,
                                      policies=rt.policies)
            out["exported_to"] = exp["exported_to"]
        return out
