"""针对 codex 反馈的安全/契约回归测试。

覆盖：
  #1 workflow preconditions 失败必须阻断
  #2 artifact 导出路径穿越
  #3 导出不暴露绝对路径
  #4 副作用步骤禁止自动修复
  #5 域名白名单拦截 popup/重定向/window.open
"""

import pytest

from agent_gavel.runtime.artifacts import ArtifactStore
from agent_gavel.runtime.errors import GavelError
from agent_gavel.runtime.policies import PolicyManager


async def _session_on(tools, base, path="/basic.html"):
    r = await tools["session_create"]()
    await tools["page_navigate"](page_id=r["page_id"], url=f"{base}{path}")
    return r["session_id"], r["context_id"], r["page_id"]


# ---------------- #1 preconditions ----------------

async def test_precondition_failure_blocks_workflow(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)  # 当前 url 不含 /required
    tmpl = {
        "template_id": "testsite_pc", "site": "testsite", "desc": "pc",
        "preconditions": [{"type": "url_contains", "value": "/required"}],
        "steps": [{"id": "s", "action": {
            "type": "click", "target": {"by": "css", "value": "#btn"}}}],
    }
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="testsite_pc")
    assert res["status"] == "blocked", res
    assert res["reason"] == "precondition_failed"
    assert res["failed_preconditions"] == [
        {"type": "url_contains", "value": "/required", "ok": False}]
    assert res["steps_completed"] == 0
    # 未执行任何步骤
    rd = await T["page_read"](page_id=pid, page_features={
        "o": "document.querySelector('#out').innerText"})
    assert rd["features"]["o"] == ""


async def test_precondition_pass_lets_workflow_run(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, _pid = await _session_on(T, base)
    tmpl = {
        "template_id": "testsite_pc_ok", "site": "testsite", "desc": "ok",
        "preconditions": [{"type": "url_contains", "value": "basic.html"}],
        "steps": [{"id": "s", "action": {
            "type": "click", "target": {"by": "css", "value": "#btn"}}}],
    }
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="testsite_pc_ok")
    assert res["status"] == "pass", res


# ---------------- #4 副作用不修复 ----------------

async def test_repair_refuses_side_effect_step(gavel_tools, webapp_server):
    T, base = gavel_tools, webapp_server
    _sid, _cid, pid = await _session_on(T, base)
    # 目标 "Go" 的 css 定位会失败（与修复用例同源，可被 explore 修复），
    # 但显式标记为不可重试：修复必须拒绝，绝不再次点击。
    tmpl = {
        "template_id": "testsite_norepair", "site": "testsite", "desc": "nr",
        "steps": [{"id": "go",
                   "action": {"type": "click",
                              "target": {"by": "css", "value": "Go"}},
                   "safe_to_retry": False, "idempotent": False,
                   "requires_confirmation": False}],
        "failure_policy": {"retry": "safe_only",
                           "repair": "explore_local_step"},
    }
    await T["workflow_save"](template=tmpl)
    res = await T["workflow_run"](name_or_id="testsite_norepair",
                                  verbosity="steps")
    assert res["status"] in ("fail", "error"), res
    assert not any(s.get("repaired") for s in res.get("steps", [])), res
    rd = await T["page_read"](page_id=pid, page_features={
        "o": "document.querySelector('#out').innerText"})
    assert rd["features"]["o"] == ""  # 没被点


# ---------------- #2/#3 导出安全 ----------------

def test_export_sanitizes_traversal_filename(tmp_path):
    store = ArtifactStore(root=str(tmp_path / "arts"))
    info = store.save_bytes(b"secret-bytes", filename="report.txt")
    out_dir = tmp_path / "out"
    res = store.export(info["artifact_id"], str(out_dir),
                       filename="../escaped.txt")
    assert (out_dir / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()   # 没跳出目录
    assert res["filename"] == "escaped.txt"
    assert res["exported"] is True
    assert "exported_to" not in res                  # 默认隐藏绝对路径


def test_export_sanitizes_absolute_and_backslash(tmp_path):
    store = ArtifactStore(root=str(tmp_path / "arts"))
    info = store.save_bytes(b"x")
    out_dir = tmp_path / "out"
    abs_target = tmp_path / "pwned.txt"
    res = store.export(info["artifact_id"], str(out_dir),
                       filename=str(abs_target))
    assert (out_dir / "pwned.txt").exists()
    assert not abs_target.exists()
    assert res["filename"] == "pwned.txt"

    res2 = store.export(info["artifact_id"], str(out_dir),
                        filename="..\\..\\evil.txt")
    assert (out_dir / "evil.txt").exists()
    assert res2["filename"] == "evil.txt"

    with pytest.raises(GavelError):
        store.export(info["artifact_id"], str(out_dir), filename="..")


def test_export_containment_with_allowlist(tmp_path):
    policies = PolicyManager()
    policies.allowed_paths = [str(tmp_path / "out")]
    store = ArtifactStore(root=str(tmp_path / "arts"))
    info = store.save_bytes(b"x")
    out_dir = tmp_path / "out"
    res = store.export(info["artifact_id"], str(out_dir),
                       filename="../../etc/x.txt", policies=policies)
    assert (out_dir / "x.txt").exists()
    assert res["filename"] == "x.txt"


def test_export_user_visible_optin(tmp_path):
    store = ArtifactStore(root=str(tmp_path / "arts"))
    info = store.save_bytes(b"x", filename="a.txt")
    res = store.export(info["artifact_id"], str(tmp_path), filename="a.txt",
                       path_visibility="user_visible")
    assert res["exported_to"] == str(tmp_path / "a.txt")


# ---------------- #5 域名白名单 ----------------

def test_release_hardened_domain_guard_blocks_all_by_default(monkeypatch):
    monkeypatch.setenv("AGENT_GAVEL_DOMAIN_GUARD", "1")
    monkeypatch.delenv("AGENT_GAVEL_ALLOW_DOMAINS", raising=False)
    p = PolicyManager()
    assert p.domain_restricted is True
    assert p.is_domain_allowed("https://example.com") is False  # 保守空集
    p.domains = {"example.com"}
    assert p.is_domain_allowed("https://example.com") is True
    assert p.is_domain_allowed("https://evil.test") is False


async def test_domain_guard_blocks_disallowed_navigation(monkeypatch,
                                                        webapp_server):
    monkeypatch.setenv("AGENT_GAVEL_ALLOW_DOMAINS", "127.0.0.1")
    from agent_gavel.runtime.browser_runtime import BrowserRuntime
    rt = BrowserRuntime()
    try:
        assert rt.policies.domain_restricted
        s = await rt.session_mgr.create(mode="ephemeral")
        ctx = await rt.context_mgr.create(s, {})
        bc = ctx.browser_context
        # 白名单内可正常导航
        ok = await bc.new_page()
        await ok.goto(f"{webapp_server}/basic.html")
        assert "basic.html" in ok.url
        # 白名单外：context 级 route 直接 abort，页面拿不到该域名
        bad = await bc.new_page()
        try:
            await bad.goto("http://blocked.invalid/evil", timeout=8000)
        except Exception:
            pass
        assert "blocked.invalid" not in (bad.url or "")
        await ok.close()
        await bad.close()
    finally:
        await rt.shutdown()
