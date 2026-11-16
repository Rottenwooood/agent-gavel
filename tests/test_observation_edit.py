"""Observation（统一 diff）与"录制后可编辑"的契约测试。

覆盖用户明确要求的四组能力：
1. diff 必须看见新页/下载/弹窗（过去 pages.opened 漏检）
2. 断言可以直接读 diff
3. 录制时的新页面用相对引用，重放时按 diff 重新绑定
4. 静默执行：只有 observe 或失败时才吐中间结果；失败保留现场并可续跑
5. 录制后可编辑：参数默认值/深度参数化/引用点清单/patch/历史回退
"""


async def _session_on(T, base, path="/basic.html"):
    r = await T["context_create"]()
    await T["page_navigate"](page_id=r["active_page_id"], url=f"{base}{path}")
    return r["context_id"], r["context_id"], r["active_page_id"]


# ---------------- 1. diff 的观测面 ----------------


async def test_diff_reports_opened_page(gavel_tools, webapp_server):
    """target=_blank 开新页：这是过去完全漏掉的一类副作用。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/popup.html")
    r = await T["locator_click"](page_id=pid, target={"by": "css", "value": "#open-tab"})
    assert r["status"] == "ok", r
    opened = ((r.get("diff") or {}).get("pages") or {}).get("opened") or []
    assert opened, r
    assert opened[0]["opener_page_id"] == pid
    assert opened[0]["page_id"] != pid


async def test_diff_reports_download(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/download.html")
    r = await T["locator_click"](page_id=pid, target={"by": "css", "value": "#dl"})
    arts = (r.get("diff") or {}).get("artifacts") or []
    assert arts, r
    assert arts[0]["artifact_id"].startswith("artifact_")


async def test_diff_reports_dialog(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/dialog.html")
    await T["page_handle_dialog"](page_id=pid, policy="auto_dismiss")
    r = await T["locator_click"](page_id=pid, target={"by": "css", "value": "#confirm-btn"})
    dialogs = (r.get("diff") or {}).get("dialogs") or []
    assert dialogs and dialogs[0]["type"] == "confirm", r


async def test_no_change_means_empty_diff(gavel_tools, webapp_server):
    """契约：没变化就没有 diff 字段，不用 unchanged 占位。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    r = await T["locator_hover"](page_id=pid, target={"by": "css", "value": "#btn"})
    assert r["status"] == "ok", r
    assert "diff" not in r or not r["diff"].get("dom"), r


# ---------------- 2. 断言读 diff ----------------


async def test_assertion_reads_diff(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/popup.html")
    ok = await T["locator_click"](
        page_id=pid,
        target={"by": "css", "value": "#open-tab"},
        assertions={
            "opened": {"read": "diff", "path": "pages.opened", "op": "count_eq", "value": 1}
        },
        wait_s=1.0,
    )
    assert ok["status"] == "pass", ok

    bad = await T["locator_click"](
        page_id=pid,
        target={"by": "css", "value": "#open-tab"},
        assertions={
            "no_open": {"read": "diff", "path": "pages.opened", "op": "count_eq", "value": 0}
        },
        wait_s=0.5,
    )
    assert bad["status"] == "fail", bad


# ---------------- 3. 新页面相对引用 ----------------


async def test_record_popup_rebinds_page(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/popup.html")
    await T["workflow_record_start"](page_id=pid)
    r = await T["locator_click"](page_id=pid, target={"by": "css", "value": "#open-tab"})
    new_pid = ((r.get("diff") or {}).get("pages") or {}).get("opened")[0]["page_id"]
    await T["locator_click"](page_id=new_pid, target={"by": "css", "value": "#btn"})
    stop = await T["workflow_record_stop"](site="testsite", desc="popup", name="popup_flow")
    assert stop["status"] == "ok", stop

    tpl = (await T["workflow_get"](name_or_id=stop["template_id"]))["template"]
    refs = [s.get("page") for s in tpl["steps"]]
    assert refs == ["page0", "new:0"], tpl

    await T["page_navigate"](page_id=pid, url=f"{base}/popup.html")
    # 点击弹过新页后 active 已跟随到新页；重放前必须显式指回要跑的页
    await T["page_select"](page_id=pid)
    res = await T["workflow_run"](name_or_id=stop["template_id"])
    assert res["status"] == "pass", res


# ---------------- 4. 静默执行 / 失败驻留 ----------------


async def test_observe_only_returns_marked_steps(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    await T["workflow_save"](
        template={
            "template_id": "ts_observe",
            "steps": [
                {
                    "id": "a",
                    "action": {
                        "type": "fill",
                        "target": {"by": "css", "value": "#text-input"},
                        "value": "x",
                    },
                },
                {
                    "id": "b",
                    "action": {"type": "click", "target": {"by": "css", "value": "#btn"}},
                    "observe": True,
                },
            ],
        }
    )
    res = await T["workflow_run"](name_or_id="ts_observe")
    assert res["status"] == "pass", res
    assert "steps" not in res  # 默认静默
    assert list(res.get("observed") or {}) == ["b"]


async def test_edit_json_add_parameterized_assertion(gavel_tools, webapp_server):
    """录完之后拿到 JSON，加一条带参数的断言，保存，再带参数调用。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")

    # 直接用 JSON 建模板（等价于录完拿到的草稿）
    await T["workflow_save"](
        template={
            "template_id": "ts_json_assert",
            "steps": [
                {"id": "a", "action": {"type": "click", "target": {"by": "css", "value": "#btn"}}}
            ],
        }
    )

    tpl = (await T["workflow_get"](name_or_id="ts_json_assert"))["template"]
    tpl["parameters"] = {"EXPECT": {"default": "clicked"}}
    tpl["steps"][0]["checkpoint"] = {
        "checks": {
            "out_ok": {
                "read": "text",
                "selector": "#out",
                "op": "eq",
                "value": "$EXPECT",
            }
        }
    }
    saved = await T["workflow_save"](template=tpl)
    assert saved["status"] == "ok" and saved["version"] == 2, saved

    ok = await T["workflow_run"](name_or_id="ts_json_assert")
    assert ok["status"] == "pass", ok

    bad = await T["workflow_run"](name_or_id="ts_json_assert", params={"EXPECT": "nope"})
    assert bad["status"] in ("fail", "ambiguous"), bad


async def test_edit_json_rewrite_chain(gavel_tools, webapp_server):
    """直接重写动作链：增删改序都在 JSON 里做，一次 save 生效。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    await T["workflow_save"](
        template={
            "template_id": "ts_json_chain",
            "steps": [
                {"id": "a", "action": {"type": "click", "target": {"by": "css", "value": "#btn"}}},
                {"id": "b", "action": {"type": "click", "target": {"by": "css", "value": "#btn"}}},
            ],
        }
    )
    tpl = (await T["workflow_get"](name_or_id="ts_json_chain"))["template"]
    # 删 b、改 a 的动作、在最前面插一步
    tpl["steps"] = [st for st in tpl["steps"] if st["id"] != "b"]
    tpl["steps"][0]["action"] = {
        "type": "fill",
        "target": {"by": "css", "value": "#text-input"},
        "value": "hi",
    }
    tpl["steps"][0]["observe"] = True
    tpl["steps"].insert(
        0,
        {"id": "n", "action": {"type": "navigate", "url": f"{base}/basic.html"}},
    )
    saved = await T["workflow_save"](template=tpl)
    assert saved["status"] == "ok", saved

    res = await T["workflow_run"](name_or_id="ts_json_chain")
    assert res["status"] == "pass", res
    assert list(res["observed"]) == ["a"], res
    rd = await T["page_read"](
        page_id=pid, page_features={"v": "document.querySelector('#text-input').value"}
    )
    assert rd["features"]["v"] == "hi"


async def test_failure_keeps_scene_and_resumes(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    await T["workflow_save"](
        template={
            "template_id": "ts_resume",
            "steps": [
                {
                    "id": "a",
                    "action": {"type": "click", "target": {"by": "css", "value": "#btn"}},
                    "checkpoint": {"type": "text_eq", "selector": "#out", "value": "WRONG"},
                },
                {"id": "b", "action": {"type": "click", "target": {"by": "css", "value": "#btn"}}},
            ],
        }
    )
    res = await T["workflow_run"](name_or_id="ts_resume")
    # 断言失败且页面确有实质变化 → 三态语义下是 ambiguous（fail 只用于"没变"）
    assert res["status"] in ("fail", "ambiguous"), res
    assert res.get("resume_hint"), res

    # 现场必须还在：run 未被注销、页面未关
    st = await T["browser_status"]()
    assert st["counts"]["pages"] >= 1

    # 改 JSON 放宽断言 → 保存 → 续跑（从失败步继续，不重放已完成步骤）
    tpl = (await T["workflow_get"](name_or_id="ts_resume"))["template"]
    tpl["steps"][0]["checkpoint"] = {
        "checks": {
            "out_ok": {"read": "text", "selector": "#out", "op": "contains", "value": "click"}
        }
    }
    saved = await T["workflow_save"](template=tpl)
    assert saved["status"] == "ok", saved

    resumed = await T["workflow_resume"](run_id=res["run_id"])
    assert resumed["status"] == "pass", resumed


async def test_parameterize_substring_and_default(gavel_tools, webapp_server):
    """参数要能改"包含在长字符串里的固定值"，且带默认值可直接跑。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    await T["workflow_record_start"](page_id=pid)
    await T["locator_fill"](
        page_id=pid, target={"by": "css", "value": "#text-input"}, value="hello world"
    )
    stop = await T["workflow_record_stop"](
        site="testsite", desc="p", name="deep_param", parameterize={"QUERY": "hello"}
    )
    assert stop["status"] == "ok", stop
    # 录完直接拿到草稿 JSON
    assert stop["template"]["steps"][0]["action"]["value"] == "$QUERY world", stop

    tpl = (await T["workflow_get"](name_or_id=stop["template_id"]))["template"]
    assert tpl["steps"][0]["action"]["value"] == "$QUERY world", tpl
    assert tpl["parameters"]["QUERY"]["default"] == "hello", tpl

    # 不传参数 → 用默认值
    r1 = await T["workflow_run"](name_or_id=stop["template_id"])
    assert r1["status"] == "pass", r1
    rd = await T["page_read"](
        page_id=pid, page_features={"v": "document.querySelector('#text-input').value"}
    )
    assert rd["features"]["v"] == "hello world"

    # 传参数 → 覆盖默认值
    r2 = await T["workflow_run"](name_or_id=stop["template_id"], params={"QUERY": "bye"})
    assert r2["status"] == "pass", r2
    rd = await T["page_read"](
        page_id=pid, page_features={"v": "document.querySelector('#text-input').value"}
    )
    assert rd["features"]["v"] == "bye world"


async def test_parameterize_covers_target_and_checkpoint(gavel_tools, webapp_server):
    """target 与 checkpoint 里的固定值同样要能参数化（旧实现改不到）。"""
    T, base = gavel_tools, webapp_server
    _s, _c, pid = await _session_on(T, base, "/basic.html")
    await T["workflow_record_start"](page_id=pid)
    await T["locator_click"](page_id=pid, target={"by": "css", "value": "#btn"})
    stop = await T["workflow_record_stop"](
        site="testsite", desc="t", name="target_param", parameterize={"SEL": "#btn"}
    )
    assert stop["status"] == "ok", stop
    assert stop["template"]["steps"][0]["action"]["target"]["value"] == "$SEL", stop
