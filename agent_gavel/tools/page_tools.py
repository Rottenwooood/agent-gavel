"""M2：导航/读取/快照/探索/截图/PDF MCP 工具（docs/tech-plan.md §8）。"""

import asyncio

from ..core.errors import GavelError
from ..runtime import get_runtime
from .common import backend, ids, rec, run_with_optional_assert
from .common import resolve_context as _resolve_context

# 软拦截特征：200 但被重定向到登录/权限页，或极短正文带拦截词
_BLOCK_URL_HINTS = ("/auth", "/login", "/sso", "/ipauth", "/error", "/403", "/denied")
_BLOCK_TEXT_HINTS = ("没有访问", "仅限校内", "请先登录", "访问受限", "验证码", "无权限")


async def _handle_or_open(page_id=None):
    """省略 page_id 时惰性建会话/上下文/页面，省掉一次 session.create 冷启动。"""
    from ..runtime import get_runtime

    rt = get_runtime()
    if page_id:
        return rt.page_mgr.get(page_id)
    try:
        return rt.page_mgr.resolve()
    except GavelError as e:
        if e.code != "no_page":
            raise
    # 没有页面时优先复用现有会话的上下文；只有完全没有会话才新建（默认会话单例）
    for session in list(rt.sessions.values()):
        if not session.context_ids:
            continue
        ctx = rt.context_mgr.get(session.context_ids[0])
        try:
            return rt.page_mgr.resolve(None, context_id=ctx.context_id)
        except GavelError:
            return await rt.page_mgr.open(ctx)
    session = await rt.session_mgr.create(mode="ephemeral", container="isolated")
    ctx = await rt.context_mgr.create(session, {"container": "isolated"})
    return rt.page_mgr.resolve(None, context_id=ctx.context_id)


async def _download_artifact(handle, url, rt):
    """导航触发下载时把产物交回去，而不是报错让模型猜下一步。"""
    import asyncio as _asyncio
    import time as _time

    before = {a["artifact_id"] for a in rt.artifacts.list()}
    deadline = _time.monotonic() + 15
    while _time.monotonic() < deadline:
        fresh = [a for a in rt.artifacts.list() if a["artifact_id"] not in before]
        if fresh:
            art = fresh[0]
            aid = art["artifact_id"]
            filename = art.get("suggested_filename") or ""
            mime = art.get("mime_type") or ""
            readable = mime.startswith("text/") or "pdf" in mime or "word" in mime
            hint = (
                f"这是文件不是网页：用 artifact(op=read_text, artifact_id={aid}) 读取"
                if readable
                else f"这是二进制文件（{mime or '未知类型'}），read_text 不适用；"
                f"需要时用 artifact(op=export, artifact_id={aid}) 落盘"
            )
            return {
                "status": "ok",
                **ids(handle),
                # 页面没有跳转：final_url 保持当前页，下载地址单独给出
                "final_url": handle.page.url,
                "navigated": False,
                "redirected": False,
                "title": await handle.page.title(),
                "status_code": None,
                "artifact_id": aid,
                "kind": art.get("kind"),
                "suggested_filename": filename or None,
                "mime_type": mime or None,
                "download_url": url,
                "warning": "navigated_to_download",
                "hint": hint,
            }
        await _asyncio.sleep(0.1)
    raise GavelError("download_timeout", "导航触发下载，但 15s 内没有落盘", retryable=True)


async def _load(
    handle,
    url,
    *,
    rt,
    detail,
    wait_until,
    timeout_s,
    assertions,
    wait_s,
    fail_on_http_error,
    min_text_chars,
    text_chars,
):
    """open / navigate 共用的加载管线：状态、正文、软拦截、下载都只在这里判定。"""
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
            text_len = await handle.actor.submit("content_len", lambda: be.content_len(handle))
        except Exception:
            text_len = None
        if isinstance(text_len, int):
            out["text_len"] = text_len

        body = None
        if text_chars and text_chars > 0:
            try:
                page_text = await handle.actor.submit("page_text", lambda: be.text(handle))
            except Exception:
                page_text = {}
            body = page_text.get("text")
            if isinstance(body, str):
                out["text"] = body[:text_chars]
                if len(body) > text_chars:
                    out["text_truncated"] = True
            # 导航类操作只在 full 下回 anchors：compact 下正文才是要读的东西
            if detail == "full" and page_text.get("anchors"):
                out["anchors"] = page_text["anchors"]

        if isinstance(text_len, int):
            if min_text_chars is not None and text_len < min_text_chars:
                out["status"] = "fail"
                out["reason"] = "empty_page"
            elif text_chars and text_len < 100 and await be.has_attachment_links(handle):
                # 字少但能下附件：真正的内容大概在文件里，这才值得提醒
                out.setdefault("warning", "sparse_content")
            elif min_text_chars is None and text_len < 20:
                prev = out.get("warning")
                out["warning"] = f"{prev} page_looks_empty".strip() if prev else "page_looks_empty"

        final = str(execution.get("final_url") or "")
        restricted = execution.get("redirected") and any(
            hint in final.lower() for hint in _BLOCK_URL_HINTS
        )
        if not restricted and isinstance(text_len, int) and text_len < 150 and body:
            restricted = any(hint in body for hint in _BLOCK_TEXT_HINTS)
        if restricted:
            # 工具本身没崩，但这一页不可用：用 warning 让模型一轮就知道此路不通
            out["page_status"] = "blocked"
            out["warning"] = "access_restricted"

    try:
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
    except GavelError as e:
        if e.code != "download_started":
            raise
        return await _download_artifact(handle, url, rt)


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
        text_chars: int = 4000,
        detail: str = "compact",
    ):
        "HTTP错误可设失败；min_text_chars防空页；默认带回正文摘要，text_chars=0关闭。"
        rt = get_runtime()
        if not url:
            raise GavelError("bad_args", "需要 url")
        handle = await _handle_or_open(page_id)
        return await _load(
            handle,
            url,
            rt=rt,
            detail=detail,
            wait_until=wait_until,
            timeout_s=timeout_s,
            assertions=assertions,
            wait_s=wait_s,
            fail_on_http_error=fail_on_http_error,
            min_text_chars=min_text_chars,
            text_chars=text_chars,
        )

    @catalog.operation(
        returns="Navigation",
        action="navigate",
        fields={"wait_until": {"enum": ["load", "domcontentloaded", "networkidle", "commit"]}},
    )
    async def page_open(
        context_id: str = None,
        session_id: str = None,
        url: str = None,
        activate: bool = True,
        wait_until: str = "domcontentloaded",
        timeout_s: float = 30.0,
        assertions: dict = None,
        wait_s: float = 6.0,
        fail_on_http_error: bool = False,
        min_text_chars: int = None,
        text_chars: int = 4000,
        detail: str = "compact",
    ):
        "新建标签页并加载；入参与返回同 navigate，activate=False不抢active。"
        rt = get_runtime()
        ctx = await _resolve_context(context_id, session_id)
        handle = await rt.page_mgr.open(ctx, None, activate=activate)
        if not url:
            return {
                "status": "ok",
                **ids(handle),
                "final_url": "about:blank",
                "navigated": False,
                "redirected": False,
                "title": "",
                "status_code": None,
            }
        return await _load(
            handle,
            url,
            rt=rt,
            detail=detail,
            wait_until=wait_until,
            timeout_s=timeout_s,
            assertions=assertions,
            wait_s=wait_s,
            fail_on_http_error=fail_on_http_error,
            min_text_chars=min_text_chars,
            text_chars=text_chars,
        )

    @catalog.operation(
        returns="Snapshot",
        fields={"level": {"enum": ["summary", "compact", "interactive", "full"]}},
    )
    async def page_snapshot(
        page_id: str = None, level: str = "summary", limit: int = 50, detail: str = "compact"
    ):
        "summary摘要/compact锚点/interactive元素/full再含HTML；compact下元素只留tag/text/href/anchor。"
        from ..runtime import get_runtime

        rt = get_runtime()
        handle = rt.page_mgr.resolve(page_id)
        be = backend()
        res = await handle.actor.submit(
            "snapshot",
            lambda: be.retry_on_nav_race(
                handle,
                lambda: be.snapshot(handle, level=level, limit=limit, compact=(detail != "full")),
            ),
        )
        return {"status": "ok", **ids(handle), **res}

    @catalog.operation(
        returns="Text", fields={"mode": {"enum": ["text", "content", "html", "links"]}}
    )
    async def page_text(
        page_id: str = None,
        selector: str = None,
        mode: str = "text",
        max_chars: int = 50000,
        max_links: int = 500,
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
        if mode == "links":
            links = res.get("links", [])
            res["links"] = links[:max_links]
            res["total"] = len(links)
        elif isinstance(res.get("text"), str) and len(res["text"]) > max_chars:
            res["text"] = res["text"][:max_chars]
            res["truncated"] = True
        return {"status": "ok", **ids(handle), "untrusted": True, **res}

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
            try:
                exp = await asyncio.to_thread(
                    rt.artifacts.export, info["artifact_id"], save_to, policies=rt.policies
                )
            except GavelError as e:
                # 导出只是顺带动作：路径被拦不该让整个 PDF 失败
                out["exported"] = False
                out["warning"] = "export_path_blocked"
                out["allowed_dirs"] = rt.policies.allowed_paths
                out["hint"] = e.message
            else:
                out["exported"] = True
                out["exported_filename"] = exp["filename"]
        return out
