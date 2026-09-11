"""DOM 通道的 MCP 工具（与 AT-SPI 桌面工具分离，server.py 只留桌面）。

T2 拆分：DOM 网页操作相关工具全部集中于此，通过 register_dom_tools(mcp)
注入同一个 mcp server。server.py 保留桌面(AT-SPI)工具。
"""

import asyncio
import json
import time
import urllib.request

from .adapter import DomClient
from .verify import dom_act_and_verify, _write_log, _feature_expr, _passes


def _dom_error(e):
    """把 DOM 通道异常转成结构化结果，绝不让 MCP 层包成 'Error executing tool'。

    关键：agent 需要看到失败原因（尤其环境类：Chrome 没起/CDP 断/无页面），
    而不是一个笼统的 UnexpectedToolError 猜半天。
    识别常见环境错误并给 hint：
      - chrome not available / CDP not ready / DISPLAY 未设置 → 环境问题
      - no matching CDP page target → 无页面
      - 其余 → 通用 error
    """
    msg = str(e)
    if any(k in msg for k in ("chrome not available", "CDP not ready", "DISPLAY")):
        return {
            "status": "error",
            "reason": "chrome_env",
            "error": msg,
            "hint": "调试 Chrome 没起来——调 chrome(ensure)，或确认 DISPLAY；"
                    "无桌面会话可在 MCP 客户端 environment 里设 AGENT_GAVEL_HEADLESS=1",
        }
    if "no matching CDP page target" in msg:
        return {
            "status": "error",
            "reason": "no_page_target",
            "error": msg,
            "hint": "Chrome 在跑但没有可用页面 tab——导航一个 URL 或开新 tab",
        }
    return {"status": "error", "reason": "dom_error", "error": msg}


async def _wait_selector(client, selector, timeout):
    """轮询等选择器命中的元素出现。返回 True/False。"""
    deadline = time.monotonic() + timeout
    js = f"!!document.querySelector({json.dumps(selector)})"
    while time.monotonic() < deadline:
        try:
            if await client.eval_js(js):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.1)
    return False


def _head_content_type(url, timeout=6.0):
    """HEAD 请求拿 Content-Type（公开 URL；失败返回 None）。"""
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.headers.get("Content-Type") or "").split(";")[0].strip()
    except Exception:
        return None


def _looks_like_error(url, title):
    """最终页是否像错误页（url/title 含 error/出错/404 等）。"""
    u = (url or "").lower()
    if "404" in u or any(k in u for k in ("error", "notfound", "not-found")):
        return True
    s = f"{u} {title or ''}".lower()
    return any(k in s for k in ("出错", "无法访问", "禁止访问", "not found",
                                "404", "page not found", "error response"))


def _same_site(a, b):
    """两个 URL 是否同主域（忽略子域/端口/path）——判断重定向是否算"到达"。

    www.bing.com → cn.bing.com、或 URL 仅追加 &rdr 跟踪参数 → 同主域，算到达。
    """
    from urllib.parse import urlparse
    try:
        ha = (urlparse(a or "").hostname or "").lower()
        hb = (urlparse(b or "").hostname or "").lower()
    except Exception:
        return False
    if not ha or not hb:
        return (a or "") == (b or "")

    def _reg(h):
        parts = h.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h

    return _reg(ha) == _reg(hb)


def _download(url, timeout=30.0):
    """HTTP 下载文件，返回 (bytes, content_type)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        return r.read(), ctype


def _extract_document(data, ctype):
    """按类型抽取文档文本。返回 (text|None, kind)。

    PDF 用 pypdf；.docx 用 python-docx；.doc（老 OLE 格式）尝试 antiword/catdoc
    （需系统装了才有效）。都先看 magic bytes（content-type 常不准）。
    """
    import io
    ct = (ctype or "").lower()
    if data[:4] == b"%PDF" or "pdf" in ct:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in reader.pages), "pdf"
        except Exception as e:
            return None, f"pdf-error:{str(e)[:120]}"
    if data[:2] == b"PK" or "wordprocessingml" in ct or "officedocument" in ct:
        try:
            from docx import Document
            d = Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append("\t".join(c.text for c in row.cells))
            return "\n".join(parts), "docx"
        except Exception as e:
            return None, f"docx-error:{str(e)[:120]}"
    if data[:4] == b"\xd0\xcf\x11\xe0" or "msword" in ct:
        return _extract_legacy_doc(data)
    return None, ct or "unknown"


def _extract_legacy_doc(data):
    """老 .doc（OLE 复合文档）：优先 soffice/libreoffice，回退 antiword/catdoc。"""
    import os as _os
    import shutil as _shutil
    import subprocess as _sp
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        # LibreOffice headless（最通用；独立 profile 避免与已开实例冲突）
        soffice = _shutil.which("soffice") or _shutil.which("libreoffice")
        if soffice:
            outdir = tempfile.mkdtemp()
            profile = tempfile.mkdtemp()
            try:
                _sp.run([soffice, "--headless",
                         f"-env:UserInstallation=file://{profile}",
                         "--convert-to", "txt:Text", "--outdir", outdir, path],
                        capture_output=True, timeout=90)
                base = _os.path.splitext(_os.path.basename(path))[0] + ".txt"
                txtpath = _os.path.join(outdir, base)
                if _os.path.isfile(txtpath):
                    with open(txtpath, encoding="utf-8", errors="ignore") as fh:
                        return fh.read(), "doc"
            except Exception:
                pass
            finally:
                _shutil.rmtree(outdir, ignore_errors=True)
                _shutil.rmtree(profile, ignore_errors=True)
        # 回退 antiword / catdoc
        for tool in ("antiword", "catdoc"):
            exe = _shutil.which(tool)
            if not exe:
                continue
            try:
                r = _sp.run([exe, path], capture_output=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.decode("utf-8", "ignore"), "doc"
            except Exception:
                continue
        return None, "doc-no-converter"
    finally:
        try:
            _os.remove(path)
        except Exception:
            pass


def register_dom_tools(mcp):
    """把 DOM 通道全部 MCP 工具注册到给定 mcp server。"""

    @mcp.tool()
    async def chrome(action: str = "ensure"):
        """管理 agent-gavel 的调试用 Chrome（T1 自管，无需手动开）。

        action:
          ensure  -> 确保在跑（探测；无则用独立 profile 自启；死了自动重拉）
          stop    -> 停掉 agent-gavel 自己拉起的 Chrome（绝不碰外部手动开的）
          status  -> 只读探测当前状态
        """
        from agent_gavel.browser_manager import (stop_own, status as bm_status,
                                                 ensure_chrome)
        if action == "stop":
            return stop_own()
        if action == "status":
            return bm_status()
        return ensure_chrome()

    @mcp.tool()
    async def dom_step(
        action: str,
        selectors: dict = None,
        page_features: dict = None,
        expected_feature: dict = None,
        *,
        debug: int = 0,
        wait_s: float = 6.0,
        trusted: bool = True,
        wait_mode: str = "poll",
        strict: bool = False,
        diff: bool = True,
        wait_navigation: bool = None,
        redact: list = None,
    ):
        """通用 DOM 单步闭环：对任意选择器执行一个动作并验证，不绑任何站点。

        边界（别混用）：本工具只做"页面内动作 + 判定"。导航用 dom_navigate
        （唯一导航入口，会验证是否真到达）；纯读值用 dom_read（不判定/不重试，
        更快）；读文本/链接/元素锚点/文件分别用 dom_text / dom_explore /
        dom_document。

        这是 AI 现场试错/建流程的核心原语。action 是原子动作（按输入通道参数化，
        覆盖人类绝大部分网页操作）：
          键盘: press_key -> selectors.keys 发任意键/组合键，如 "Enter"/"Tab"/
                "Ctrl+A"/"Shift+Tab"/"ArrowDown"/"F5"（修饰键 + 前缀）
          文本: type_text -> selectors.text(或 value) 真实输入任意文本到当前焦点
                （先 focus/click 目标）；含中文/emoji
          鼠标: click     -> selectors.target 点击；selectors.button=left|right|
                middle，selectors.count=1|2（右键/双击）
          悬停: hover     -> selectors.target 鼠标悬停（触发 tooltip/hover 态）
          拖拽: drag      -> selectors.source 拖到 selectors.destination（或 dx/dy）
          滚动: scroll    -> selectors.direction=down|up, selectors.amount(屏数),
                selectors.target 指定滚动容器(可选, 缺省滚整页)
        便捷动作（组合原语的糖，旧模板兼容）：
          set_value -> selectors.input + selectors.value 填框（内部真实清空+输入）
          press_enter -> 发 Enter（等价 press_key keys="Enter"）
          clear     -> selectors.input 清空
          focus     -> selectors.input 仅聚焦（不清空）
        目标寻址: 任意选择器可用 CSS 或锚点 __text__:完整文本 /
          __text_nth__:N::文本（同名按钮按序号）。
        page_features: {特征名: JS表达式}，动作后提取页面状态
        expected_feature: {特征名: {op: eq|neq|exists|not_exists|contains, value}}
        trusted: True（默认）用 CDP 真实输入/点击/按键（isTrusted=true）。对 React
          重渲染站点（知乎登录、部分 SaaS）合成事件会被框架冲掉/忽略，必须 trusted；
          普通站（百度/B站）合成事件即可，为提速可显式 trusted=False。
        wait_mode: poll（默认，兼容旧行为，定时重查断言）
                 | event（断言下沉页面，MutationObserver 事件驱动等待——适合提交后
                  等结果出现的异步长等待，DOM 变化即刻唤醒，无定时轮询；
                  注意只对"变化反映到 DOM"的断言有效）
        strict: True 时不自动降级重试，失败直接返回（测试/调试用，暴露真实 fail）。
        diff: True（默认）启用 scoped diff 兜底：无 expected_feature 时用动作前后
          DOM 变化程序判定 pass/ambiguous（取代盲 sleep 放行）；有断言但失败且页面
          确有实质变化时返回 ambiguous 而非直接 fail——只在拿不准处让模型介入。
        wait_navigation: 点击/按键可能触发跳转——True 时轮询等跳转完成再验证，
          返回最终 url（默认 None 只做即时判断，不拖慢普通点击）。
        debug: 1 保留 evidence 并写日志。
        redact: 可选，声明 selectors 里哪些字段值是敏感值(不落日志/不回传)，
          如 ["value"]（填密码时用）。dom_run_template 的 $PASSWORD 等自动脱敏。
        例：设值并断言输入框内容：
           dom_step("set_value",
             selectors={"input":"#inp-query","value":"流浪地球"},
             page_features={"v":"document.querySelector('#inp-query').value"},
             expected_feature={"v":{"op":"eq","value":"流浪地球"}})
        例：按键+断言：
           dom_step("press_key", selectors={"keys":"Enter"},
             page_features={"t":"document.title"},
             expected_feature={"t":{"op":"contains","value":"结果"}})
        """
        if action == "navigate":
            return {"status": "error", "reason": "use_dom_navigate",
                    "hint": "导航请用 dom_navigate（唯一导航入口，会验证是否真到达、"
                            "返回最终 url / content_type）；dom_step 只做页面内动作。"
                            "纯读值用 dom_read。"}
        # 收集要脱敏的值（redact 列出的 selectors 字段的实际值）
        redact_values = []
        if redact and selectors:
            for k in redact:
                v = (selectors or {}).get(k)
                if v:
                    redact_values.append(str(v))
        try:
            async with DomClient() as client:
                return await dom_act_and_verify(
                    client,
                    action=action,
                    selectors=selectors,
                    page_features=page_features,
                    expected_feature=expected_feature,
                    wait_s=wait_s,
                    debug=debug,
                    log_prefix="dom_step",
                    trusted=trusted,
                    wait_mode=wait_mode,
                    strict=strict,
                    diff=diff,
                    wait_navigation=wait_navigation,
                    redact_values=redact_values,
                )
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_navigate(site: str = None, url: str = None, *,
                           page_features: dict = None,
                           expected_feature: dict = None,
                           wait_s: float = 8.0, debug: int = 0):
        """导航浏览器到指定站点/URL，并验证是否真的到达（不再"发出即成功"）。

        这是**唯一的导航入口**——dom_step 不接受 navigate。导航一律用这个。

        url: 直接跳这个 URL（给了就用它）。
        site: 可选；只有没给 url 时，才用它查已存模板的 home 跳转。
        page_features / expected_feature: 可选，到达后按断言判定（语义同 dom_step）；
          断言在 wait_s 内**轮询等待**（页面动态内容晚于 load 事件出现时不会误判）。
        wait_s: 断言轮询上限（秒，默认 8）。
        返回 requested_url / final_url / navigated / redirected / content_type。
        内置判定：最终 url 没变（目标其实是下载/doc/pdf，Chrome 不导航）→ fail；
          跳到 */error* 或标题像错误页 → warning。
        """
        target = url
        if not target and site:
            from .templates import load_template
            tmpl = load_template(site)
            target = (tmpl or {}).get("home")
        if not target:
            return {"status": "error", "error": "no url and no template home for site",
                    "hint": "pass url= directly, or dom_save_template first"}
        t0 = time.monotonic()
        try:
            async with DomClient() as client:
                try:
                    url_before = await client.eval_js("location.href")
                except Exception:
                    url_before = None
                await client.navigate(target)
                await client.wait_page_load()
                final_url = await client.eval_js("location.href")
                title = await client.get_title()
                navigated = bool(final_url and final_url != url_before)
                ctype = await asyncio.to_thread(_head_content_type, target)
                out = {
                    "requested_url": target,
                    "final_url": final_url,
                    "url": final_url,
                    "title": title,
                    "navigated": navigated,
                    "redirected": bool(navigated and final_url != target),
                    "content_type": ctype,
                    "cost": {"elapsed_ms": int((time.monotonic() - t0) * 1000)},
                }
                # 可选断言——在 wait_s 内轮询等待（页面动态内容可能晚于 load 事件出现，
                # 一次性评估会误判 fail）
                assertion_ok = None
                if page_features:
                    deadline = time.monotonic() + wait_s
                    feats, errs, checks = {}, {}, []
                    while True:
                        feats, errs = {}, {}
                        for name, expr in page_features.items():
                            try:
                                feats[name] = await client.eval_js(_feature_expr(expr))
                            except Exception as ex:
                                errs[name] = str(ex)[:200]
                        if not expected_feature:
                            break  # 只读特征，不判定
                        checks, all_pass = [], True
                        for name, exp in expected_feature.items():
                            passed = _passes(exp, feats.get(name))
                            checks.append({"feature": name, "op": exp.get("op", "eq"),
                                           "actual": feats.get(name),
                                           "expected": exp.get("value"),
                                           "passed": passed})
                            if not passed:
                                all_pass = False
                        if all_pass:
                            assertion_ok = True
                            break
                        if time.monotonic() >= deadline:
                            assertion_ok = False
                            break
                        await asyncio.sleep(0.1)
                    out["features"] = feats
                    if errs:
                        out["feature_errors"] = errs
                    if expected_feature:
                        out["checks"] = checks
                        if not assertion_ok:
                            out["status"] = "fail"
                            out["reason"] = "assertion_timeout"
                            out["hint"] = (f"到达页面但 {wait_s}s 内断言未满足——"
                                           "确认特征/期望值，或加大 wait_s")
                            if debug:
                                _write_log("dom_navigate",
                                           {"site": site, "url": target}, out)
                            return out
                # 断言等待期间页面可能还在变——重读 url/title 供内置判定
                try:
                    final_url = await client.eval_js("location.href")
                    title = await client.get_title()
                    out["final_url"] = out["url"] = final_url
                    out["title"] = title
                    out["navigated"] = bool(final_url and final_url != url_before)
                except Exception:
                    pass
                # 内置判定（错误页优先；其次"是否到达请求的 URL"）
                # 同主域（含区域重定向 www→cn、或仅追加 &rdr 跟踪参数）也算到达
                reached_target = bool(final_url and target and (
                    final_url == target
                    or final_url.rstrip("/") == str(target).rstrip("/")
                    or _same_site(final_url, target)))
                out["reached_target"] = reached_target
                if assertion_ok:
                    out["status"] = "pass"
                elif _looks_like_error(final_url, title):
                    out["status"] = "warning"
                    out["reason"] = "error_page"
                    out["hint"] = "最终页面疑似错误页（url/title 含 error）"
                elif reached_target:
                    out["status"] = "pass"
                elif not navigated:
                    out["status"] = "fail"
                    out["reason"] = "not_navigated"
                    out["hint"] = (f"最终 url 未变（{url_before}）且没到达请求的 URL"
                                   f"——目标可能是下载/非 HTML（content_type={ctype}）。"
                                   "文件类用 dom_document 读内容")
                else:
                    # 导航发生了但落到别的 URL（重定向/中转页/验证码墙）——不是明确 pass
                    out["status"] = "warning"
                    out["reason"] = "redirected_off_target"
                    out["hint"] = (f"未到达请求的 URL，落到了 {final_url}"
                                   "（重定向 / 中转页 / 验证码墙？）")
                if debug:
                    _write_log("dom_navigate", {"site": site, "url": target}, out)
                return out
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_read(page_features: dict, selector: str = None, *,
                       wait_s: float = 0.0, debug: int = 0):
        """只读取值：按 page_features 到页面取值返回——不判定、不重试、不 diff。

        这是**通用读**。读文本/链接、枚举可交互元素、读文件分别有省事的专用工具
        dom_text / dom_explore / dom_document——它们本质是这里的特化视图；能用
        表达式做的，用它们更省事（不必手写 JS）。要动作/判定用 dom_step。

        与 dom_step 的区别：不做成功/失败判定，不触发降级重试，不抓 diff 快照。
        纯一次页面求值（毫秒级）。适合读标题/输入框内容/正文/PDF 文字层等。
        之前只能故意制造断言失败才能拿到值，且要跑全策略重试（慢）；这个工具
        直接把值给你。

        page_features: {名字: 表达式或函数}。纯表达式直接算；需要多步/赋值时
          写成函数形式 "() => { ...; return ...; }"（换行在函数体里合法）。
        selector: 可选，先等该元素出现再取值；没命中返回 status=no_match。
        返回 status: ok | no_match(selector 没命中) | partial_error(部分表达式抛错)
          | error(全部抛错)；features 是成功取到的值；errors 是抛错的；
          empty 是命中但值为空的名字列表（区分"没选到"与"选到但是空"）。
        wait_s: selector 存在时的等待上限（秒）。
        """
        t0 = time.monotonic()
        try:
            async with DomClient() as client:
                if selector:
                    found = (await _wait_selector(client, selector, wait_s)
                             if wait_s > 0
                             else await client.eval_js(
                                 f"!!document.querySelector({json.dumps(selector)})"))
                    if not found:
                        return {"status": "no_match", "selector": selector,
                                "hint": "选择器没命中任何元素——检查选择器或加 wait_s",
                                "url": await client.eval_js("location.href"),
                                "title": await client.get_title()}
                feats, errors, empty = {}, {}, []
                for name, expr in (page_features or {}).items():
                    try:
                        v = await client.eval_js(_feature_expr(expr))
                    except Exception as ex:
                        errors[name] = str(ex)[:200]
                        continue
                    feats[name] = v
                    if v is None or v == "" or v == [] or v == {}:
                        empty.append(name)
                if errors and feats:
                    status = "partial_error"
                elif errors:
                    status = "error"
                elif feats and len(empty) == len(feats):
                    status = "empty"
                else:
                    status = "ok"
                out = {
                    "status": status,
                    "features": feats,
                    "url": await client.eval_js("location.href"),
                    "title": await client.get_title(),
                    "cost": {"elapsed_ms": int((time.monotonic() - t0) * 1000)},
                }
                if errors:
                    out["errors"] = errors
                if empty:
                    out["empty"] = empty
                if debug:
                    _write_log("dom_read", {"page_features": page_features}, out)
                return out
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_text(selector: str = None, mode: str = "text", *,
                       wait_s: float = 0.0, max_chars: int = 50000,
                       max_links: int = 500):
        """取页面/元素文本（正文提取）——dom_read 的专用视图（省去手写 JS）。

        selector: CSS 选择器；缺省取整页 body。
        mode: text(可见文字) | content(原始文字) | html(结构) |
              links(所有链接 [{text, href}]，省去手写 JS/翻 HTML)。
        wait_s: 元素等待上限（秒），0=不等待。
        max_chars: 返回文本上限（默认 50000），超出截断并标 truncated。
        max_links: mode=links 时的链接数上限（默认 500）。
        注意：浏览器内置 PDF 阅读界面不是页面元素，取不到；站点自渲染的 PDF
        文字层（如 .textLayer）可正常取。
        """
        t0 = time.monotonic()
        if mode == "links":
            root = json.dumps(selector) if selector else "document.body"
            expr = (
                "(() => {"
                f"  const root = {root};"
                "  if (!root) return {found:false, links:[]};"
                "  const seen = new Set(); const out = [];"
                "  for (const a of root.querySelectorAll('a[href]')) {"
                "    const href = a.href; if (seen.has(href)) continue; seen.add(href);"
                "    const text = (a.innerText||a.textContent||'').trim().slice(0,200);"
                "    out.push({text, href});"
                "  }"
                "  return {found:true, links: out};"
                "})()"
            )
        else:
            sel = json.dumps(selector) if selector else "null"
            getter = {"html": "el.outerHTML", "content": "el.textContent"}.get(
                mode, "el.innerText")
            expr = (
                "(() => {"
                f"  const sel = {sel};"
                "  const el = sel ? document.querySelector(sel) : document.body;"
                "  if (!el) return {found:false, text:''};"
                f"  const t = ({getter}) || '';"
                "  return {found:true, text:t, tag:el.tagName.toLowerCase()};"
                "})()"
            )
        try:
            async with DomClient() as client:
                if selector and wait_s > 0:
                    await _wait_selector(client, selector, wait_s)
                r = await client.eval_js(expr)
                if not isinstance(r, dict) or not r.get("found"):
                    return {"status": "not_found", "selector": selector,
                            "hint": "元素没找到——检查选择器或加 wait_s",
                            "url": await client.eval_js("location.href")}
                if mode == "links":
                    links = r.get("links") or []
                    return {
                        "status": "ok",
                        "mode": "links",
                        "links": links[:max_links],
                        "count": len(links),
                        "truncated": len(links) > max_links,
                        "url": await client.eval_js("location.href"),
                        "title": await client.get_title(),
                        "cost": {"elapsed_ms": int((time.monotonic() - t0) * 1000)},
                    }
                text = r.get("text") or ""
                out = {
                    "status": "ok",
                    "mode": mode,
                    "text": text[:max_chars],
                    "length": len(text),
                    "truncated": len(text) > max_chars,
                    "tag": r.get("tag"),
                    "url": await client.eval_js("location.href"),
                    "title": await client.get_title(),
                    "cost": {"elapsed_ms": int((time.monotonic() - t0) * 1000)},
                }
                # 正文极短：内容可能在 iframe 内嵌文档或附件里——主动发现并提示
                if len(text.strip()) < 50:
                    try:
                        extra = await client.eval_js(
                            "(() => {"
                            " const f=[...document.querySelectorAll('iframe')]"
                            "   .map(x=>x.src).filter(Boolean).slice(0,5);"
                            " const a=[...document.querySelectorAll('a[href]')]"
                            "   .filter(x=>/\\.(pdf|docx?|xlsx?)([?#]|$)/i.test(x.href))"
                            "   .map(x=>({text:(x.innerText||'').trim().slice(0,60),href:x.href}))"
                            "   .slice(0,10);"
                            " return {iframes:f, attachments:a};"
                            "})()")
                        if isinstance(extra, dict) and (extra.get("iframes")
                                                        or extra.get("attachments")):
                            if extra.get("iframes"):
                                out["iframes"] = extra["iframes"]
                            if extra.get("attachments"):
                                out["attachments"] = extra["attachments"]
                            out["hint"] = ("正文很短——内容可能在 iframe 内嵌文档或附件里。"
                                           "用 dom_document(url=iframe 的 src 或附件链接) 读")
                    except Exception:
                        pass
                return out
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_document(url: str = None, max_chars: int = 200000):
        """下载并抽取文档文本（PDF / Word）——dom_read 读不到的"文件"专用视图。

        很多政策文件是 .pdf/.doc/.docx 直链，Chrome 会下载而非导航，DOM 通道读不到
        （dom_navigate 会返回 not_navigated）。这个工具用 HTTP 直接取文件并按类型抽文本。

        url: 文件 URL；缺省用当前页 URL。
        max_chars: 返回文本上限。
        返回 content_type + text（能抽则抽）+ extracted(bool) + kind。
        .doc（老二进制格式）暂不支持抽取，返回 status=unsupported + content_type。
        """
        t0 = time.monotonic()
        target = url
        try:
            if not target:
                async with DomClient() as client:
                    target = await client.eval_js("location.href")
            if not target or not str(target).startswith(("http://", "https://")):
                return {"status": "error", "reason": "no_http_url",
                        "error": f"需要 http(s) URL（当前 {target}）",
                        "hint": "给 url= 参数，或先 dom_navigate 到一个 http 页面"}
            data, ctype = await asyncio.to_thread(_download, target)
            out = {"url": target, "content_type": ctype, "bytes": len(data),
                   "cost": {"elapsed_ms": int((time.monotonic() - t0) * 1000)}}
            text, kind = _extract_document(data, ctype)
            if text is None:
                out["status"] = "unsupported"
                out["extracted"] = False
                out["kind"] = kind
                if kind == "doc-no-converter":
                    out["hint"] = ("老 .doc 需系统装 antiword 或 catdoc 才能抽取"
                                   "（如 `apt install antiword` / `apt install catdoc`）；"
                                   "否则下载后用办公软件打开")
                else:
                    out["hint"] = (f"content_type={ctype}——暂不支持抽取。"
                                   "HTML 用 dom_text 读；其它文件下载后用办公软件打开")
                return out
            out["status"] = "ok"
            out["extracted"] = True
            out["kind"] = kind
            out["text"] = text[:max_chars]
            out["length"] = len(text)
            out["truncated"] = len(text) > max_chars
            return out
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_resolve(url: str):
        """解析跳转链到真实 URL（baidu.com/link、搜狗/360 等重定向链）。

        只读：HTTP 跟随重定向，返回 final_url / redirect_chain。对 302 型有效；
        对纯 JS 跳转（需真执行脚本）拿不到，会返回 no_redirect + hint 让你改用
        dom_navigate 打开看最终 url。
        """
        if not url or not str(url).startswith(("http://", "https://")):
            return {"status": "error", "reason": "no_http_url",
                    "error": "需要 http(s) URL"}

        def _follow():
            chain = []

            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    chain.append(newurl)
                    return super().redirect_request(req, fp, code, msg, headers, newurl)

            opener = urllib.request.build_opener(_NoRedirect)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with opener.open(req, timeout=15) as r:
                    return r.geturl(), chain
            except Exception:
                return None, chain

        try:
            final, chain = await asyncio.to_thread(_follow)
            if final and final != url:
                out = {"status": "ok", "requested_url": url,
                       "final_url": final, "redirect_chain": chain}
                fl = final.lower()
                if _looks_like_error(final, "") or any(
                        k in fl for k in ("captcha", "验证", "wappoc")):
                    out["status"] = "warning"
                    out["reason"] = "final_looks_blocked"
                    out["hint"] = ("解析出的最终 URL 像验证码/错误页——真实内容可能拿不到"
                                   "（如微信需验证）")
                return out
            return {"status": "no_redirect", "requested_url": url,
                    "final_url": final or url,
                    "hint": "未发生 HTTP 重定向——可能是 JS 跳转（需真导航），"
                            "用 dom_navigate 打开看最终 url"}
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_explore(
        tag: str = None,
        text_contains: str = None,
        head: int = None,
        tail: int = None,
        include_all: bool = False,
        include_hidden: bool = False,
        url: str = None,
    ):
        """探索当前浏览器页面：返回可交互元素的锚点清单——dom_read 的专用视图
        （锚点带唯一性校验，比手写表达式省事）。

        锚点分三类：id（#kw）、name（input[name=q]）、文本（__text__: 或
        __text_nth__:N::，重复文本用组内序号区分）。

        裁剪参数（给 agent 选择权，避免 200+ 元素全塞进上下文）：
          tag: 只返回指定标签（input/button/a/form/select/textarea）
          text_contains: 只返回文本含此关键词的元素
          head: 只返回前 N 个（页面顶部元素）
          tail: 只返回后 N 个（页面底部元素，如确认按钮/弹窗）
          include_all: True 时返回全部（含 no-unique-selector），默认只返锚点
          include_hidden: True 时也返回不可见元素（默认过滤掉）
          url: 可选，先导航到该 URL 再探索（一次性，省去先调 dom_navigate）
        返回 status: ok | no_match（过滤后 0 命中，附 hint 说明原因）。
        """
        try:
            async with DomClient() as client:
                if url:
                    await client.navigate(url)
                    await client.wait_page_load()
                items = await client.explore()
                total = len(items)

                if not include_hidden:
                    items = [it for it in items if it.get("visible", True)]
                if not include_all:
                    items = [it for it in items if it.get("selector")]
                if tag:
                    items = [it for it in items if it.get("tag") == tag]
                if text_contains:
                    items = [it for it in items
                             if text_contains in (it.get("text") or "")]

                head_total = len(items)
                if head is not None and head > 0:
                    items = items[:head]
                elif tail is not None and tail > 0:
                    items = items[-tail:]

                out = {
                    "status": "ok" if head_total else "no_match",
                    "url": await client.eval_js("location.href"),
                    "title": await client.get_title(),
                    "total_elements": total,
                    "after_filter": head_total,
                    "returned": len(items),
                    "head": head,
                    "tail": tail,
                    "elements": items,
                }
                if head_total == 0:
                    if total == 0:
                        out["hint"] = ("当前页没有可交互元素——可能是静态/纯文本页，"
                                       "或内容是 PDF/图片。读正文用 dom_text、"
                                       "取值用 dom_read、取链接用 dom_text(mode=links)")
                    else:
                        extra = ""
                        if tag:
                            try:
                                cnt = await client.eval_js(
                                    f"document.querySelectorAll({json.dumps(tag)}).length")
                                if cnt:
                                    extra = (f"页面有 {cnt} 个 <{tag}> 元素，但都不在可交互集合"
                                             f"或不可见——加 include_hidden=True 试试；")
                                else:
                                    extra = f"页面没有 <{tag}> 元素；"
                            except Exception:
                                pass
                        out["hint"] = (f"共 {total} 个可交互元素，过滤后 0 命中。{extra}"
                                       "找链接用 dom_text(mode=links)、读值用 dom_read、"
                                       "读正文用 dom_text")
                return out
        except Exception as e:
            return _dom_error(e)

    @mcp.tool()
    async def dom_save_template(site: str, desc: str, steps: list,
                                home: str = None, name: str = None,
                                sensitive_params: list = None):
        """把现场跑通的一套网页流程固化成可复用模板（存到用户目录，跨版本持久）。

        site: 纯网站名（bing/zhihu/douban…），文件名自动 = site_功能.json。
        steps 每项 = {"action": navigate|press_key|type_text|click|hover|drag|
                      scroll|set_value|press_enter|clear|focus,
                      "selectors": {...},
                      "page_features": {特征名: JS表达式},   # 动作后提取
                      "expected_feature": {特征名: {op, value}}}  # 断言
        可变输入用 $VAR 占位（如 "$QUERY"/"$USERNAME"）——保存时自动提取进
        params 声明，dom_list_templates 可查；运行时由 dom_run_template 的
        params 传入真实值。**不要写死真实值/密码进模板**。
        sensitive_params: 可选，显式声明哪些 $VAR 是敏感的(密码/token)，
          如 ["PASSWORD"] → 模板 params 标 sensitive，运行时真实值不落日志/
          不进返回。不声明默认不敏感(仅名含 PASSWORD/TOKEN 等关键词时兜底)。
        例：
          [{"action":"navigate","selectors":{"url":"https://.../"},
            "page_features":{"title":"document.title"},
            "expected_feature":{"title":{"op":"exists"}}},
           {"action":"set_value","selectors":{"input":"#q","value":"$QUERY"},
            "page_features":{"input_value":"..."},
            "expected_feature":{"input_value":{"op":"eq","value":"$QUERY"}}}]
        """
        from .templates import save_template
        params_spec = None
        if sensitive_params:
            params_spec = {p: {"sensitive": True} for p in sensitive_params}
        r = save_template(site, desc, steps, home=home, name=name,
                          params_spec=params_spec)
        r["status"] = "saved"
        return r

    @mcp.tool()
    async def dom_list_templates():
        """列出已保存的 DOM 流程模板，含每模板所需 params（含 sensitive 标记）。

        返回每项: {file, site, desc, steps, params: {参数名: {sensitive: bool}},
                  consecutive_fails, suspected}。用 params 看跑该模板要传哪些
        参数（dom_run_template 的 params 键）。
        """
        from .templates import list_templates
        return list_templates()

    @mcp.tool()
    async def dom_template_stats():
        """查看所有 DOM 模板的失效检测 stats。

        返回 {模板文件名: {consecutive_fails, suspected, last, last_at}}。
        suspected=true 表示连续失败达阈值、模板疑似失效。
        """
        from .templates import all_stats
        return all_stats()

    @mcp.tool()
    async def dom_run_template(name_or_site: str, params: dict = None, *,
                               debug: int = 0, wait_s: float = 6.0,
                               strict: bool = False):
        """执行已保存的 DOM 流程模板，逐步验证，任一步 fail 即停。

        name_or_site: 模板文件名或 site 标识。
        params: 替换模板里的 $VAR 占位符（如 {"QUERY": "搜索词"}）。模板声明的
          参数(dom_list_templates 可查)必须都传，缺参数返回 missing_params。
          敏感参数(模板标 sensitive 的，如 $PASSWORD)真实值不落日志/不进返回。
        strict: True 时不自动降级重试（测试/调试用，暴露模板真实 fail）。
        debug: 1 保留 evidence 并写日志。
        失效检测：每跑完记一次连续失败；连续失败>=3 标 suspected，
        pass 清零。suspected 模板执行时返回结果顶部带 warning。
        """
        from .templates import (resolve_template, _fill, record_run, get_stats,
                                norm_params)
        template_file, tmpl = resolve_template(name_or_site)
        if not tmpl:
            return {"status": "not_found", "name": name_or_site,
                    "available": [t["file"] for t in _list_template_summaries()]}
        # 规整模板参数声明（新=对象带 sensitive，老=数组；敏感以模板显式标记为准）
        pdecl = norm_params(tmpl.get("params"), tmpl.get("steps"))
        need = list(pdecl.keys())
        params = params or {}
        missing = [p for p in need if p not in params]
        if missing:
            return {"status": "fail", "reason": "missing_params",
                    "missing": missing, "needs": need,
                    "desc": tmpl.get("desc"),
                    "hint": f"dom_run_template 需传参数: {need}"}
        # 敏感参数值收集——模板声明 sensitive 的才脱敏
        # (is_sensitive_var 关键词兜底已在 norm_params 里对未声明老参数生效)
        redact_values = [str(params[p]) for p in params
                         if pdecl.get(p, {}).get("sensitive") and params[p]]
        # 执行前查失效状态
        pre = get_stats(template_file)
        warning = None
        if pre.get("suspected"):
            warning = (f"模板疑似失效(连续失败 {pre.get('consecutive_fails')} 次)——"
                       f"建议 dom_explore 重新探索后 dom_save_template 覆盖")
        filled = _fill(tmpl, params)
        results = []
        try:
            async with DomClient() as client:
                for i, step in enumerate(filled.get("steps", [])):
                    r = await dom_act_and_verify(
                        client,
                        action=step["action"],
                        selectors=step.get("selectors"),
                        page_features=step.get("page_features"),
                        expected_feature=step.get("expected_feature"),
                        wait_s=wait_s,
                        debug=debug,
                        log_prefix=f"tmpl_{filled.get('site')}_s{i}",
                        trusted=step.get("trusted", True),
                        wait_mode=step.get("wait_mode", "poll"),
                        strict=strict,
                        redact_values=redact_values,
                    )
                    results.append({"step": i, "action": step["action"], **r})
                    if r.get("status") != "pass":
                        record_run(template_file, "fail")
                        out = {
                            "status": "fail",
                            "failed_step": i,
                            "desc": tmpl.get("desc"),
                            "results": results,
                        }
                        if warning:
                            out["warning"] = warning
                        return out
            record_run(template_file, "pass")
            out = {"status": "pass", "desc": tmpl.get("desc"),
                   "steps_total": len(filled.get("steps", [])), "results": results}
            if warning:
                out["warning"] = warning
            return out
        except Exception as e:
            env = _dom_error(e)
            env["failed_step"] = len(results)
            env["desc"] = tmpl.get("desc")
            env["results"] = results
            return env

    def _list_template_summaries():
        from .templates import list_templates
        return list_templates()
