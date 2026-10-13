"""M1：浏览器/session/context/page 生命周期 MCP 工具（docs/tech-plan.md §8）。

统一约定：
- 所有失败归一为结构化 dict（GavelError.to_dict），绝不让 MCP 包成通用异常
- 列表返回值包成 {"items": [...]}（避免 MCP SDK 把 list 拆成多个 TextContent）
- 资源参数可省略：无 page_id 时用 active page
"""

import functools
import os

from ..backends.playwright_backend import PlaywrightBackend
from ..runtime import get_runtime
from ..runtime.errors import GavelError, wrap_exception

_ATTACH_WARNING = (
    "attach 模式：正在接管一个已存在的浏览器。注意——你控制的是用户真实账号环境，"
    "本机任何进程都能通过该调试端口操作它；本项目不会关闭它；运行期间用户关闭浏览器"
    "会导致会话中断；同一资料目录存在单实例限制。仅在你明确需要时使用。")


def _mode_is_attach(browser_mode, cdp_endpoint=None):
    mode = (browser_mode or os.environ.get("AGENT_GAVEL_BROWSER_MODE")
            or "").strip().lower()
    endpoint = cdp_endpoint or os.environ.get("AGENT_GAVEL_CDP_ENDPOINT")
    return mode == "attach" or bool(endpoint)


def _launch_opts_match(handle, *, headless, channel, proxy, browser_mode,
                       cdp_endpoint):
    """已有默认浏览器与请求参数是否一致（None 表示不限定）→ 用于 browser_launch 幂等。"""
    if handle is None or not handle.alive:
        return False
    o = handle.opts or {}
    if headless is not None and bool(o.get("headless")) != bool(headless):
        return False
    if proxy is not None and bool(o.get("proxy")) != bool(proxy):
        return False
    if channel is not None and o.get("channel") != channel:
        return False
    if _mode_is_attach(browser_mode, cdp_endpoint) and handle.mode != "attach":
        return False
    return True


def _guard(fn):
    """把 GavelError / 未知异常转成结构化结果。"""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except GavelError as e:
            return e.to_dict()
        except Exception as e:  # noqa: BLE001
            return wrap_exception(e).to_dict()
    return wrapper


async def _resolve_session(session_id=None):
    rt = get_runtime()
    if session_id:
        return rt.session_mgr.get(session_id)
    if len(rt.sessions) == 1:
        return next(iter(rt.sessions.values()))
    if not rt.sessions:
        raise GavelError("session_required", "还没有 session",
                         hint="先 session_create")
    raise GavelError("session_ambiguous", "有多个 session，请显式指定 session_id",
                     detail={"sessions": list(rt.sessions)})


async def _resolve_context(context_id=None, session_id=None):
    rt = get_runtime()
    if context_id:
        return rt.context_mgr.get(context_id)
    session = await _resolve_session(session_id)
    if session.context_ids:
        return rt.context_mgr.get(session.context_ids[0])
    raise GavelError("context_not_found", "该 session 下没有 context",
                     hint="先 context_create")


def register_browser_tools(mcp):
    rt_get = get_runtime
    backend = PlaywrightBackend(None)  # 用 runtime 的方法签名即可，无需实例态

    # ---------------- browser ----------------

    @mcp.tool()
    @_guard
    async def browser_launch(headless: bool = None, channel: str = None,
                             proxy: dict = None, browser_mode: str = None,
                             cdp_endpoint: str = None):
        """启动/确保默认浏览器进程。

        默认 **managed（C1）**：自起加固 Chrome（持久真实资料目录）+ Playwright 连接；
        `browser_mode="attach"` + `cdp_endpoint` 则**接管**一个已存在的浏览器（C2，见警告）；
        `browser_mode="playwright"` 回到 Playwright 直接启动。
        headless 省略时按 AGENT_GAVEL_HEADLESS 决定（默认有头）。
        """
        rt = rt_get()
        explicit = any(x is not None for x in
                       (headless, channel, proxy, browser_mode, cdp_endpoint))
        if not explicit:
            handle = await rt.ensure_default_browser()
        elif _launch_opts_match(rt.default_handle, headless=headless,
                                channel=channel, proxy=proxy,
                                browser_mode=browser_mode,
                                cdp_endpoint=cdp_endpoint):
            handle = rt.default_handle          # 幂等：已存在且参数一致，直接复用
        else:
            if rt.default_handle is not None:
                await rt.processes.close(rt.default_handle)
            rt._default_handle = await rt.processes.launch(
                headless=headless, channel=channel, proxy=proxy,
                browser_mode=browser_mode, cdp_endpoint=cdp_endpoint)
            handle = rt._default_handle
        out = {"status": "ok", **handle.to_dict(),
               "counts": rt.status()["counts"]}
        if handle.mode == "attach" or _mode_is_attach(browser_mode, cdp_endpoint):
            out["warning"] = _ATTACH_WARNING
        return out

    @mcp.tool()
    @_guard
    async def browser_status():
        """只读：当前进程/session/context/page 计数、事件与指标快照。"""
        rt = rt_get()
        return {"status": "ok", **rt.status()}

    @mcp.tool()
    @_guard
    async def browser_close():
        """关闭所有浏览器进程与资源（runtime 仍可继续用，下次调用会重拉）。"""
        rt = rt_get()
        await rt.shutdown()
        rt.mark_open()
        return {"status": "ok", "closed": True}

    # ---------------- session ----------------

    @mcp.tool()
    @_guard
    async def session_create(mode: str = "ephemeral", headless: bool = None,
                             proxy: dict = None, channel: str = None,
                             locale: str = None, timezone_id: str = None,
                             viewport: dict = None, device: str = None,
                             user_data_dir: str = None, storage_state: str = None,
                             browser_mode: str = None, cdp_endpoint: str = None,
                             container: str = None):
        """创建隔离 session（ephemeral / persistent / clone）。

        container（容器策略）：
          default（默认）= 用资料目录的**默认容器**：继承 profile 的登录/cookie，
                  改动累积回 profile（可迭代）。
          isolated      = 新建隔离容器：继承 profile 的 cookie，但不写回 profile。
        ephemeral：共享默认浏览器上建容器。persistent：独立进程 + 持久资料目录。
        clone：从 storage_state 起（默认用 isolated 容器，适合多账号）。
        browser_mode="attach" + cdp_endpoint：接管一个已存在浏览器（C2，见返回 warning）。
        """
        rt = rt_get()
        config = dict(headless=headless, proxy=proxy, channel=channel,
                      locale=locale, timezone_id=timezone_id, viewport=viewport,
                      device=device, user_data_dir=user_data_dir,
                      storage_state=storage_state, browser_mode=browser_mode,
                      cdp_endpoint=cdp_endpoint, container=container)
        session = await rt.session_mgr.create(mode=mode, **config)
        ctx_id = session.context_ids[0] if session.context_ids else None
        ctx = rt.contexts.get(ctx_id)
        page_id = ctx.active_page_id if ctx else None
        out = {"status": "ok", **session.to_dict(),
               "context_id": ctx_id, "page_id": page_id}
        handle = session.handle
        if handle is not None and handle.mode == "attach":
            out["warning"] = _ATTACH_WARNING
        return out

    @mcp.tool()
    @_guard
    async def session_list():
        """列出所有 session。"""
        rt = rt_get()
        return {"status": "ok",
                "items": [s.to_dict() for s in rt.sessions.values()]}

    @mcp.tool()
    @_guard
    async def session_get(session_id: str):
        """取单个 session 详情（含 contexts / busy 状态）。"""
        rt = rt_get()
        session = rt.session_mgr.get(session_id)
        out = {"status": "ok", **session.to_dict(),
               "contexts": [rt.contexts[c].to_dict()
                            for c in session.context_ids if c in rt.contexts]}
        return out

    @mcp.tool()
    @_guard
    async def session_reset(session_id: str):
        """按原配置重建 session（关旧开新，保留 mode 与 config）。"""
        rt = rt_get()
        session = await rt.session_mgr.reset(session_id)
        return {"status": "ok", **session.to_dict()}

    @mcp.tool()
    @_guard
    async def session_close(session_id: str):
        """关闭 session 及其全部 context/page。"""
        rt = rt_get()
        return {"status": "ok", **await rt.session_mgr.close(session_id)}

    @mcp.tool()
    @_guard
    async def session_export_state(session_id: str, path: str = None):
        """导出 storage state（cookie/localStorage）。

        给了 path 则写文件（受文件白名单约束）；否则直接返回 state。
        """
        rt = rt_get()
        session = rt.session_mgr.get(session_id)
        if not session.context_ids:
            raise GavelError("context_not_found", "该 session 没有 context")
        ctx = rt.contexts[session.context_ids[0]]
        state = await ctx.browser_context.storage_state()
        if path:
            rt.policies.check_local_path(path, purpose="导出 storage state")
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            return {"status": "ok", "session_id": session_id, "path": path}
        return {"status": "ok", "session_id": session_id, "storage_state": state}

    @mcp.tool()
    @_guard
    async def session_import_state(path: str, mode: str = "clone"):
        """从 storage_state 文件创建新 session（默认 clone 模式）。"""
        rt = rt_get()
        rt.policies.check_local_path(path, purpose="导入 storage state")
        session = await rt.session_mgr.create(mode=mode, storage_state=path)
        ctx_id = session.context_ids[0] if session.context_ids else None
        ctx = rt.contexts.get(ctx_id)
        return {"status": "ok", **session.to_dict(), "context_id": ctx_id,
                "page_id": ctx.active_page_id if ctx else None}

    # ---------------- context ----------------

    @mcp.tool()
    @_guard
    async def context_create(session_id: str = None, locale: str = None,
                             timezone_id: str = None, viewport: dict = None,
                             device: str = None, permissions: list = None,
                             extra_http_headers: dict = None,
                             storage_state: dict = None,
                             container: str = "isolated"):
        """在指定 session 下新建容器（不同账号/cookie/语言/设备）。

        container 默认 isolated：新建隔离容器（继承 profile 的 cookie，但不写回）；
        传 "default" 则复用 profile 默认容器。
        """
        rt = rt_get()
        session = await _resolve_session(session_id)
        config = dict(locale=locale, timezone_id=timezone_id, viewport=viewport,
                      device=device, permissions=permissions,
                      extra_http_headers=extra_http_headers,
                      storage_state=storage_state, container=container)
        ctx = await rt.context_mgr.create(session, config)
        return {"status": "ok", **ctx.to_dict()}

    @mcp.tool()
    @_guard
    async def context_close(context_id: str):
        """关闭 context（级联关闭其 page）。"""
        rt = rt_get()
        return {"status": "ok", **await rt.context_mgr.close(context_id)}

    @mcp.tool()
    @_guard
    async def context_cookies_get(context_id: str, url: str = None):
        """读取 context 的 cookie（可选按 url 过滤）。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        cookies = await ctx.browser_context.cookies(url) if url \
            else await ctx.browser_context.cookies()
        return {"status": "ok", "context_id": context_id, "cookies": cookies,
                "count": len(cookies)}

    @mcp.tool()
    @_guard
    async def context_cookies_set(context_id: str, cookies: list = None):
        """写入 cookie（Playwright addCookies 格式）。

        cookie 需有 url 或 domain+path；url 缺路径会自动补 "/"
        （Playwright 拒绝无 path 的 url，如 "https://example.com"）。
        """
        from urllib.parse import urlparse

        def _norm(c):
            d = dict(c)
            u = d.get("url")
            if u and not urlparse(u).path:
                d["url"] = u.rstrip("/") + "/"
            return d

        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        if not cookies:
            raise GavelError("bad_args", "需要 cookies")
        await ctx.browser_context.add_cookies([_norm(c) for c in cookies])
        return {"status": "ok", "context_id": context_id,
                "added": len(cookies)}

    @mcp.tool()
    @_guard
    async def context_cookies_clear(context_id: str):
        """清空 context 的所有 cookie。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.clear_cookies()
        return {"status": "ok", "context_id": context_id, "cleared": True}

    @mcp.tool()
    @_guard
    async def context_set_headers(context_id: str, headers: dict = None):
        """设置 context 的额外请求头（set_extra_http_headers）。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        await ctx.browser_context.set_extra_http_headers(headers or {})
        return {"status": "ok", "context_id": context_id,
                "headers": headers or {}}

    @mcp.tool()
    @_guard
    async def context_set_permissions(context_id: str, permissions: list = None,
                                      origin: str = None):
        """授予/清空 context 权限（给出 permissions 授予，空则清空）。"""
        rt = rt_get()
        ctx = rt.context_mgr.get(context_id)
        if permissions:
            await ctx.browser_context.grant_permissions(permissions,
                                                        origin=origin)
        else:
            await ctx.browser_context.clear_permissions()
        return {"status": "ok", "context_id": context_id,
                "permissions": permissions or []}

    # ---------------- page ----------------

    @mcp.tool()
    @_guard
    async def page_open(context_id: str = None, session_id: str = None,
                        url: str = None):
        """新开一个 page（标签页），可选直接导航 URL。"""
        rt = rt_get()
        ctx = await _resolve_context(context_id, session_id)
        handle = await rt.page_mgr.open(ctx, url)
        return {"status": "ok", **handle.to_dict()}

    @mcp.tool()
    @_guard
    async def page_list():
        """列出所有 page。"""
        rt = rt_get()
        return {"status": "ok",
                "items": [p.to_dict() for p in rt.pages.values()]}

    @mcp.tool()
    @_guard
    async def page_get(page_id: str):
        """取单个 page 详情。"""
        rt = rt_get()
        return {"status": "ok", **rt.page_mgr.get(page_id).to_dict()}

    @mcp.tool()
    @_guard
    async def page_focus(page_id: str):
        """把该 page 设为 active（省略 page_id 的后续调用默认用它）。"""
        rt = rt_get()
        handle = rt.page_mgr.get(page_id)
        handle.context.active_page_id = page_id
        rt.active_page_id = page_id
        return {"status": "ok", **handle.to_dict()}

    @mcp.tool()
    @_guard
    async def page_select(page_id: str):
        """同 page_focus：选中 active page。"""
        rt = rt_get()
        handle = rt.page_mgr.get(page_id)
        handle.context.active_page_id = page_id
        rt.active_page_id = page_id
        return {"status": "ok", **handle.to_dict()}

    @mcp.tool()
    @_guard
    async def page_close(page_id: str):
        """关闭 page。"""
        rt = rt_get()
        return {"status": "ok", **await rt.page_mgr.close(page_id)}

    @mcp.tool()
    @_guard
    async def page_wait(page_id: str = None, state: str = "load",
                        timeout_s: float = 30.0):
        """等待页面加载状态（load / domcontentloaded / networkidle）。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {"status": "ok", **await handle.actor.submit(
            "page_wait",
            lambda: backend.wait_load_state(handle, state, timeout_s),
            timeout_s=timeout_s + 5)}

    @mcp.tool()
    @_guard
    async def page_reload(page_id: str = None, timeout_s: float = 30.0):
        """重新加载页面。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {"status": "ok", **await handle.actor.submit(
            "page_reload",
            lambda: backend.reload(handle, timeout_s=timeout_s),
            timeout_s=timeout_s + 5)}

    @mcp.tool()
    @_guard
    async def page_go_back(page_id: str = None, timeout_s: float = 30.0):
        """后退。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {"status": "ok", **await handle.actor.submit(
            "page_go_back",
            lambda: backend.go_back(handle, timeout_s=timeout_s),
            timeout_s=timeout_s + 5)}

    @mcp.tool()
    @_guard
    async def page_go_forward(page_id: str = None, timeout_s: float = 30.0):
        """前进。"""
        rt = rt_get()
        handle = rt.page_mgr.resolve(page_id)
        return {"status": "ok", **await handle.actor.submit(
            "page_go_forward",
            lambda: backend.go_forward(handle, timeout_s=timeout_s),
            timeout_s=timeout_s + 5)}
