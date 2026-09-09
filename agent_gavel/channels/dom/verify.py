"""DOM 版闭环执行器：复用 agent-gavel 的验证框架，但操作网页 DOM。

与 server.py 的 act_and_verify（AT-SPI/桌面）并列。共用：
  normalize / diff / wait / catalog 逻辑

每个站点（baidu/bing/google）的"页面状态"是一组 key=value：
  {"url": ..., "title": ..., "kw_value": ..., "has_result": bool}
页面本身没有可复用的无障碍树，所以用"特征提取"代替 diff——
即调用方声明"这个动作应该改变哪个特征"，然后检查该特征。

T3：断言失败降级重试。fail 不即停，按序换策略重试：
  S1 原样重试(竞态) → S2 trusted 翻转 → S3 重新 explore 换锚点 →
  S4 滚动重试 → S5 wait_mode 翻转。
strict=True 时只跑一次尝试，暴露真实 fail(测试/调试用)。
"""

import asyncio
import difflib
import datetime
import json
import os
import time

from .adapter import DomClient

LOG_DIR = os.environ.get(
    "AGENT_GAVEL_LOG_DIR",
    os.path.join(os.environ.get("APPDATA") if os.name == "nt"
                 else os.environ.get("XDG_CONFIG_HOME",
                                     os.path.expanduser("~/.config")),
                 "agent-gavel", "logs"))


def _write_log(prefix, call, result):
    """记录一次 DOM 调用（debug=1 时）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        with open(os.path.join(LOG_DIR, f"{prefix}_{ts}.json"), "w", encoding="utf-8") as f:
            json.dump({"call": call, "result": result}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _passes(expect, actual):
    """判定单个特征断言是否通过。expect: {op, value}，actual: JS 求值结果。"""
    op = expect.get("op", "eq")
    if op == "eq":
        return (actual == expect.get("value"))
    if op == "neq":
        return (actual != expect.get("value"))
    if op == "exists":
        return (actual not in (None, "", False))
    if op == "not_exists":
        return (actual in (None, "", False))
    if op == "contains":
        return (expect.get("value") in str(actual))
    return False


def _strip_evidence(result):
    """debug=0 时去掉 evidence。"""
    if "evidence" in result:
        del result["evidence"]
        result["evidence"] = {"stripped": True, "note": "pass debug=1 for evidence"}
    return result


def _redact(obj, secrets):
    """递归替换结构里等于任一 secret 的值 -> '***'（防敏感值落日志/返回）。

    只处理标量相等匹配（不误伤含敏感词的普通文本），secrets 为字符串列表。
    """
    if not secrets:
        return obj
    if isinstance(obj, str):
        return "***" if obj in secrets else obj
    if isinstance(obj, dict):
        return {k: _redact(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v, secrets) for v in obj]
    return obj


async def _find_anchor(client, selector):
    """给失效的 CSS 选择器找一个可用的替代锚点。

    selector 可能指 input(#inp-query)、target(#su 等)。通过 explore 枚举
    当前页可交互元素，找 tag/name/id 与旧选择器相关的可用锚点。
    返回新锚点字符串或 None。这是 S3 的核心——页面改版后锚点漂移时，
    用页面现状重新定位。
    """
    import re
    target_tag = None
    target_name = None
    if selector.startswith("#"):
        target_id = selector[1:]
    else:
        m = re.match(r"^([a-z]+)(?:\[name=['\"]?([^'\"]+)['\"]?\])?$", selector)
        target_tag = m.group(1) if m else None
        target_name = m.group(2) if m and m.group(2) else None
        target_id = None

    try:
        items = await client.explore()
    except Exception:
        return None

    for it in items or []:
        sel = it.get("selector")
        if not sel:
            continue
        if target_id and it.get("id") == target_id:
            return sel
        if target_name and it.get("name") == target_name:
            return sel
        if target_tag and it.get("tag") == target_tag and it.get("name"):
            return sel
    return None


# ==================== DOM diff 兜底（scoped before/after） ====================
# 背景：无显式断言(fuzzy)时原实现直接 sleep 0.5 -> pass，等于不验证；
# 断言失败也只给 fail。桌面通道靠 before/after diff + ambiguous 升级模型，
# DOM 复用同一思路：对动作作用域做轻量结构签名快照，程序判定
# pass / fail / ambiguous，把『重新看页面』留给少数拿不准的场景。

_DIFF_MAX_NODES = 6000


def _diff_snapshot_js(target_sel, scope_sel):
    """生成『取动作作用域 DOM 结构签名』的 JS 表达式（返回 {lines, bodyScope, root}）。

    target_sel: 动作目标 CSS；scope_sel: 可选显式作用域。作用域取：显式 scope >
    目标最近的容器(含 FORM/MAIN/SECTION/ARTICLE/DIV/UL/OL/TABLE/NAV) > body。
    每节点签名 = tag#id.classes[attrs]+输入值/叶子文本，前面带 URL/TITLE 两行头。
    遍历上限 _DIFF_MAX_NODES，超限截断仍可比（头行保留判定导航）。
    """
    t = json.dumps(target_sel) if target_sel else "null"
    sc = json.dumps(scope_sel) if scope_sel else "null"
    return (
        "(() => {"
        f"  const MAX = {_DIFF_MAX_NODES};"
        f"  const target = {t};"
        f"  const scopeSel = {sc};"
        "  let root = null;"
        "  if (scopeSel) root = document.querySelector(scopeSel);"
        "  if (!root && target) {"
        "    const el = document.querySelector(target);"
        "    if (el) {"
        "      let n = el;"
        "      while (n && n !== document.body && n.parentElement) {"
        "        if (/^(FORM|MAIN|SECTION|ARTICLE|DIV|UL|OL|TABLE|NAV)$/.test(n.tagName)) { root = n; break; }"
        "        n = n.parentElement;"
        "      }"
        "      if (!root) root = el.parentElement || el;"
        "    }"
        "  }"
        "  if (!root) root = document.body;"
        "  const bodyScope = (root === document.body);"
        "  const out = [];"
        "  const norm = (x) => { const s = (x == null ? '' : String(x)).replace(/\\s+/g, ' ').trim(); return s; };"
        "  out.push('URL=' + location.href); out.push('TITLE=' + norm(document.title));"
        "  let count = 0;"
        "  const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);"
        "  let el;"
        "  while ((el = w.nextNode())) {"
        "    if (++count > MAX) break;"
        "    const tag = el.tagName.toLowerCase();"
        "    let line = tag + (el.id ? '#' + el.id : '');"
        "    if (el.classList && el.classList.length) {"
        "      const cls = Array.from(el.classList).filter(c => !/^(sc-|jss|css-)/.test(c)).sort().join('.');"
        "      if (cls) line += '.' + cls;"
        "    }"
        "    for (const a of ['name','type','role','aria-expanded','aria-hidden','placeholder','alt']) {"
        "      const av = el.getAttribute(a); if (av) line += '[' + a + '=' + norm(av).slice(0, 60) + ']';"
        "    }"
        "    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') { line += '=' + norm(el.value).slice(0, 200); }"
        "    else if (el.tagName === 'SELECT') { const so = el.selectedOptions && el.selectedOptions[0]; line += '=' + norm(so ? so.text : ''); }"
        "    if (!el.children.length) { const tx = norm(el.textContent || el.getAttribute('placeholder') || ''); if (tx) line += '::' + tx.slice(0, 120); }"
        "    out.push(line);"
        "  }"
        "  return { lines: out, bodyScope: bodyScope, root: (root === document.body ? 'body' : (root.tagName + (root.id ? '#' + root.id : ''))) };"
        "})()"
    )


async def _diff_snapshot(client, target_sel=None, scope_sel=None):
    """取一次动作作用域的 DOM 结构签名。失败返回 None（调用方按无法 diff 处理）。"""
    try:
        r = await client.eval_js(_diff_snapshot_js(target_sel, scope_sel), timeout=5.0)
        if isinstance(r, dict) and isinstance(r.get("lines"), list):
            return r
    except Exception:
        pass
    return None


def _diff_verdict(before, after):
    """对比前后签名，给出 {changed, meaningful, added, removed, ratio, samples_*, scope}。

    语义：
      - URL/TITLE 头变化 => 一定 meaningful（导航级变化）
      - 非 body 作用域：任意 1 处节点增删即 meaningful（作用域小而纯净）
      - body 作用域：需 >=4 处增删才 meaningful（过滤无关噪音）
    """
    bl = (before or {}).get("lines") or []
    al = (after or {}).get("lines") or []
    if not bl or not al:
        return None
    sm = difflib.SequenceMatcher(None, bl, al)
    added = removed = 0
    s_add, s_rem = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added += (j2 - j1)
            s_add += al[j1:j2]
        if tag in ("delete", "replace"):
            removed += (i2 - i1)
            s_rem += bl[i1:i2]
    header_changed = bl[:2] != al[:2]
    body = bool(before.get("bodyScope")) or bool(after.get("bodyScope"))
    n_changed = added + removed
    if header_changed:
        meaningful = True
    else:
        if body:
            # body 作用域噪音多：需足够增删，或相对占比明显(过滤大页面零星广告位变动)
            total = max(len(bl), len(al))
            meaningful = (n_changed >= 4) or (total > 0 and n_changed / total >= 0.02)
        else:
            # 局部作用域小而纯净：任意 1 处变化即 meaningful
            meaningful = n_changed >= 1
    return {
        "changed": (added + removed) > 0,
        "meaningful": meaningful,
        "added": added,
        "removed": removed,
        "ratio": round(sm.ratio(), 4),
        "samples_added": s_add[:6],
        "samples_removed": s_rem[:6],
        "scope": before.get("root") or after.get("root"),
    }


async def _attempt(client, *, action, selectors, page_features, expected_feature,
                   wait_s, trusted, wait_mode, start, diff=True):
    """执行一次动作 + 验证。返回 (result_dict, ok_bool)。

    ok=False 表示动作执行失败(选择器失效等)；ok=True 但 status=fail 表示
    动作做了但断言没过。返回 result 含 status/verification/evidence。
    """
    sel = selectors or {}
    # diff 兜底：动作前抓 scoped 快照（仅对会产生 DOM/导航效果、且命中目标动作）。
    before = None
    diff_target = sel.get("target") or sel.get("input")
    diff_wanted = (diff and action in ("click", "press_key", "press_enter",
                                       "type_text", "set_value", "focus",
                                       "clear", "hover", "drag"))
    if diff_wanted and diff_target:
        before = await _diff_snapshot(client, diff_target, sel.get("diff_scope"))
    elif diff and action == "navigate":
        before = await _diff_snapshot(client, None, sel.get("diff_scope"))
    else:
        before = None

    r = None
    if action == "set_value":
        target = sel.get("input")
        if not target:
            return {"status": "fail", "action_error": "set_value needs selectors.input",
                    "action": {"name": action}}, False
        r = await client.set_value(target, sel.get("value", ""), trusted=trusted)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "type_text":
        # 真实输入文本到当前焦点（用前先 focus/click 目标）
        text = sel.get("text", sel.get("value", ""))
        target = sel.get("target") or sel.get("input")
        if target:
            await client.focus(target)
        r = await client.type_text(text)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "press_key":
        keys = sel.get("keys", sel.get("key", ""))
        if not keys:
            return {"status": "fail", "action_error": "press_key needs selectors.keys",
                    "action": {"name": action}}, False
        r = await client.press_key(keys, trusted=trusted)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "click":
        target = sel.get("target") or sel.get("input")
        if not target:
            return {"status": "fail", "action_error": "click needs selectors.target",
                    "action": {"name": action}}, False
        r = await client.click(target, trusted=trusted,
                               button=sel.get("button", "left"),
                               count=sel.get("count", 1))
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "hover":
        target = sel.get("target")
        if not target:
            return {"status": "fail", "action_error": "hover needs selectors.target",
                    "action": {"name": action}}, False
        r = await client.hover(target, trusted=True)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "drag":
        src = sel.get("source", sel.get("target"))
        dst = sel.get("target" if not sel.get("source") else "destination")
        r = await client.drag(src, dst or sel.get("destination"),
                              dx=sel.get("dx"), dy=sel.get("dy"), trusted=True)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "scroll":
        r = await client.scroll(sel.get("direction", "down"),
                                float(sel.get("amount", 1.0)),
                                selector=sel.get("target"))
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "press_enter":
        # 便捷动作：本质是发 Enter 键（保持旧模板兼容）
        r = await client.press_enter(trusted=trusted)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "navigate":
        r = await client.navigate(sel.get("url", ""))
        await client.wait_page_load()
        ok = True
    elif action == "focus":
        target = sel.get("input")
        r = await client.focus(target)
        ok = isinstance(r, dict) and r.get("ok")
    elif action == "clear":
        target = sel.get("input") or sel.get("target")
        if not target:
            return {"status": "fail", "action_error": "clear needs selectors.input",
                    "action": {"name": action}}, False
        r = await client.clear(target, trusted=trusted)
        ok = isinstance(r, dict) and r.get("ok")
    else:
        raise ValueError(f"unknown dom action: {action}")

    if not ok:
        return {"status": "fail", "reason": "action_failed",
                "action_error": r, "action": {"name": action}}, False

    # ---- 验证特征 ----
    async def _poll_verify(remaining_s):
        evidence = {}
        deadline = time.monotonic() + remaining_s
        status = "pass"
        detail = {"mode": "feature"}
        first = True
        all_pass = True
        while True:
            ev = {}
            try:
                for fname, expr in page_features.items():
                    ev[fname] = await client.eval_js(expr)
            except Exception:
                ev = {f: "<eval-error>" for f in page_features} if first else ev
            if first:
                evidence = ev
                first = False
            if expected_feature:
                checks = []
                all_pass = True
                for fname, expect in expected_feature.items():
                    actual = ev.get(fname)
                    passed = _passes(expect, actual)
                    checks.append({"feature": fname, "op": expect.get("op", "eq"),
                                   "actual": actual,
                                   "expected": expect.get("value"),
                                   "passed": passed})
                    if not passed:
                        all_pass = False
                if all_pass:
                    detail["checks"] = checks
                    evidence = ev
                    break
                detail["checks"] = checks
            else:
                # expected 为空：不再固定等 0.5s，交给动作后的 diff 兜底判定
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)  # 细间隔：整页跳转后新页面~130ms 就绪,
            # 0.25s 间隔会让 poll2 落到 ~380ms 白等 ~250ms(实测 442->226ms)
        status = "pass" if (not expected_feature) or all_pass else "fail"
        return status, detail, evidence

    if wait_mode == "event" and page_features and expected_feature:
        ev_pass = False
        ev_res = None
        try:
            ev_res = await client.wait_event(page_features, expected_feature,
                                             timeout_s=wait_s)
        except Exception:
            ev_res = None
        ev_pass = (isinstance(ev_res, dict) and ev_res.get("status") in ("pass", "ambiguous"))
        if ev_pass:
            evidence = ev_res.get("values", {}) if isinstance(ev_res, dict) else {}
            status = "pass"
            checks = []
            for fname, expect in (expected_feature or {}).items():
                actual = evidence.get(fname)
                checks.append({"feature": fname, "op": expect.get("op", "eq"),
                               "actual": actual,
                               "expected": expect.get("value"),
                               "passed": _passes(expect, actual)})
            detail = {"mode": "event", "checks": checks}
        else:
            remaining = wait_s - (time.monotonic() - start)
            remaining = max(remaining, 0.3)
            status, detail, evidence = await _poll_verify(remaining)
            if status == "pass":
                detail["mode"] = "event->poll_fallback"
    else:
        if page_features:
            status, detail, evidence = await _poll_verify(wait_s)
        else:
            # 无 page_features/expected：不固定睡 0.5s，直接交 diff 兜底判定
            status, detail, evidence = "pass", {"mode": "feature"}, {}

    # ---- diff 兜底判定：无显式断言(fuzzy)或断言失败时，用 scoped 前后变化兜底 ----
    verdict = None
    if diff and before is not None and (status != "pass" or not expected_feature):
        after = await _diff_snapshot(client, diff_target, sel.get("diff_scope"))
        verdict = _diff_verdict(before, after) if after else None
        if verdict is not None:
            if expected_feature:
                # 有断言：只有"断言没过但页面确实有变化"才算拿不准 -> ambiguous
                if status != "pass" and verdict.get("meaningful"):
                    detail = dict(detail)
                    detail["mode"] = "feature->diff"
                    detail["diff"] = verdict
                    status = "ambiguous"
            else:
                # 无断言：用 diff 做程序判定，不再盲 sleep 放行
                detail = dict(detail)
                detail["mode"] = "diff"
                detail["diff"] = verdict
                status = "pass" if verdict.get("meaningful") else "ambiguous"
    res = {
        "status": status,
        "action": {"name": action, "result": r},
        "verification": {
            **detail,
            "url": await client.eval_js("location.href"),
            "title": await client.get_title(),
        },
        "evidence": evidence,
        "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)},
    }
    if status == "ambiguous":
        res["reason"] = ("assertion_mismatch_but_change"
                         if expected_feature else "no_observable_change")
    elif status != "pass":
        res["reason"] = "assertion_timeout" if expected_feature else "action_failed"
    return res, True


async def dom_act_and_verify(
    client: DomClient,
    *,
    action: str,
    selectors: dict = None,
    page_features: dict = None,
    expected_feature: dict = None,
    wait_s: float = 8.0,
    debug: int = 0,
    log_prefix: str = "op",
    trusted: bool = True,
    wait_mode: str = "poll",
    strict: bool = False,
    redact_values: list = None,
    diff: bool = True,
):
    """执行网页动作 + 验证特征变化，一次调用返回。

    action: set_value | click | press_enter | navigate | focus | clear
    selectors: {目标名: CSS选择器}，action 用
    page_features: 动作后应检查的页面特征，dict {名: JS表达式返回标量}
    expected_feature: 断言期望，{名: {op: eq|neq|exists|not_exists, value: ...}}
    trusted: True 用 CDP 真实输入/点击/按键（isTrusted=true），对 React 重渲染
        站点（知乎等）合成事件会被冲掉/忽略，必须开。默认 False 保速度。
    wait_mode: poll(默认，Python 侧定时重查断言) | event(事件驱动等待)
    strict: True 时不降级，只跑一次，失败直接返回真实 fail（测试/调试用，
        用来暴露"模板本身不行"而非被降级掩盖）。默认 False 自动降级重试。

    T3 降级序列（strict=False 且动作非 navigate 时）：
      S1 原样重试(竞态) → S2 trusted 翻转 → S3 重新 explore 换锚点 →
      S4 滚动重试 → S5 wait_mode 翻转。全失败才返回最终 fail。
    """
    start = time.monotonic()
    call = {
        "action": action,
        "selectors": selectors,
        "page_features": page_features,
        "expected_feature": expected_feature,
        "debug": debug,
        "trusted": trusted,
        "wait_mode": wait_mode,
        "strict": strict,
    }
    result = {}

    try:
        if strict:
            result, _ = await _attempt(
                client, action=action, selectors=selectors,
                page_features=page_features, expected_feature=expected_feature,
                wait_s=wait_s, trusted=trusted, wait_mode=wait_mode, start=start, diff=diff)
        else:
            # 降级序列：S1 原样 → S2 trusted 翻转 → S3 换锚点 → S4 滚动 → S5 翻转 wait_mode
            # navigate 不做降级（导航语义明确，重试没意义且会重复跳转）。
            if action == "navigate":
                result, _ = await _attempt(
                    client, action=action, selectors=selectors,
                    page_features=page_features, expected_feature=expected_feature,
                    wait_s=wait_s, trusted=trusted, wait_mode=wait_mode, start=start, diff=diff)
            else:
                attempts = []
                strategies = [("retry", trusted, wait_mode)]
                if trusted:
                    strategies.append(("trusted_flip", False, wait_mode))
                else:
                    strategies.append(("trusted_flip", True, wait_mode))

                # S3: 选择器失效时换锚点
                replaced = None
                for key in ("input", "target"):
                    old = (selectors or {}).get(key)
                    if old and (old.startswith("#") or "[" in old):
                        alt = await _find_anchor(client, old)
                        if alt:
                            replaced = (key, old, alt)
                            break

                # S5: wait_mode 翻转
                alt_mode = "event" if wait_mode == "poll" else "poll"

                final = None
                for i, (name, tr, wm) in enumerate(strategies):
                    res, _ = await _attempt(
                        client, action=action, selectors=selectors,
                        page_features=page_features, expected_feature=expected_feature,
                        wait_s=wait_s, trusted=tr, wait_mode=wm, start=start, diff=diff)
                    res["strategy"] = name
                    attempts.append(res)
                    if res.get("status") in ("pass", "ambiguous"):
                        final = res
                        break
                    # 动作执行失败(选择器失效)——尝试换锚点
                    if res.get("action_error"):
                        if replaced:
                            new_sel = dict(selectors or {})
                            key, old, alt = replaced
                            new_sel[key] = alt
                            res2, _ = await _attempt(
                                client, action=action, selectors=new_sel,
                                page_features=page_features,
                                expected_feature=expected_feature,
                                wait_s=wait_s, trusted=tr, wait_mode=wm, start=start, diff=diff)
                            res2["strategy"] = "anchor_replace"
                            attempts.append(res2)
                            if res2.get("status") in ("pass", "ambiguous"):
                                final = res2
                                break

                # S4: 滚动重试（元素可能在视口外）
                if final is None:
                    try:
                        await client.eval_js("window.scrollBy(0, 400)")
                    except Exception:
                        pass
                    res4, _ = await _attempt(
                        client, action=action, selectors=selectors,
                        page_features=page_features, expected_feature=expected_feature,
                        wait_s=wait_s, trusted=trusted, wait_mode=alt_mode, start=start, diff=diff)
                    res4["strategy"] = "scroll+" + alt_mode
                    attempts.append(res4)
                    if res4.get("status") in ("pass", "ambiguous"):
                        final = res4

                # S5 独立：wait_mode 翻转（若上面 scroll 已试过 alt_mode 则跳过重复）
                if final is None:
                    if not any(a.get("strategy", "").endswith(alt_mode)
                               for a in attempts):
                        res5, _ = await _attempt(
                            client, action=action, selectors=selectors,
                            page_features=page_features,
                            expected_feature=expected_feature,
                            wait_s=wait_s, trusted=trusted, wait_mode=alt_mode,
                            start=start, diff=diff)
                        res5["strategy"] = "wait_mode_flip"
                        attempts.append(res5)
                        if res5.get("status") in ("pass", "ambiguous"):
                            final = res5

                final = final or attempts[-1]
                final["retries"] = [
                    {"strategy": a.get("strategy"), "status": a.get("status")}
                    for a in attempts]
                if final.get("status") not in ("pass", "ambiguous"):
                    final["reason"] = "all_strategies_failed"
                result = final
    except Exception as e:
        result = {"status": "error", "error": str(e),
                  "cost": {"elapsed_ms": int((time.monotonic() - start) * 1000)}}

    # 敏感值脱敏（密码/token 等）——在写日志和返回前都打码，防真值落盘/回传
    if redact_values:
        call = _redact(call, redact_values)
        result = _redact(result, redact_values)

    if debug:
        _write_log(log_prefix, call, result)
        return result
    return _strip_evidence(result)
