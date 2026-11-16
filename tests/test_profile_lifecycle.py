"""资料目录生命周期：按 profile 单独关闭、headless 不一致必须报错。"""

import os


async def test_close_single_profile_keeps_others(gavel_tools, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    T = gavel_tools
    await T["context_create"](profile="a")
    await T["context_create"](profile="b")
    root = str(tmp_path / "profiles")

    def dirs_of(st):
        return sorted(
            p["user_data_dir"] for p in st["processes"] if p.get("alive") and p["user_data_dir"].startswith(root)
        )

    st = await T["browser_status"]()
    assert [d.rsplit("/", 1)[-1] for d in dirs_of(st)] == ["a", "b"], st["processes"]

    closed = await T["browser_close"](profile="a")
    assert closed["closed"] is True
    st2 = await T["browser_status"](detail="full")
    assert [d.rsplit("/", 1)[-1] for d in dirs_of(st2)] == ["b"], st2["processes"]
    # 关掉的浏览器不得留下悬空 context
    assert st2["counts"]["contexts"] == 1, st2["counts"]
    assert len(st2["active_pages"]) == 1, st2["active_pages"]


async def test_headless_mismatch_is_error(gavel_tools, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))
    T = gavel_tools
    await T["context_create"]()  # 测试环境默认无头
    r = await T["browser_launch"](headless=False)
    assert r["status"] == "error", r
    assert r["reason"] == "headless_mismatch", r
