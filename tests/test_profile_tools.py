"""资料副本：复制选择（反识别项保留、隐私项剔除）、档位、当前副本切换。"""

import json
import os

import pytest

from agent_gavel.runtime import profiles


def _make_source(tmp_path):
    src = tmp_path / "user-chrome"
    (src / "Default" / "Local Storage" / "leveldb").mkdir(parents=True)
    (src / "Default" / "Cache").mkdir(parents=True)
    (src / "Default" / "Extensions" / "abc").mkdir(parents=True)
    (src / "Default" / "Cookies").write_bytes(b"cookies")
    (src / "Default" / "History").write_bytes(b"history")
    (src / "Default" / "Local Storage" / "leveldb" / "x.ldb").write_bytes(b"ls")
    (src / "Default" / "Cache" / "data").write_bytes(b"cache")
    (src / "Default" / "Login Data").write_bytes(b"passwords")
    (src / "Default" / "Sync Data").write_bytes(b"sync")
    (src / "Default" / "Extensions" / "abc" / "manifest.json").write_text("{}")
    (src / "Default" / "Preferences").write_text(json.dumps(
        {"profile": {"name": "Me"}, "sync": {"x": 1}, "homepage": "h"}))
    (src / "Default" / "Secure Preferences").write_text(json.dumps(
        {"sync": {}, "homepage": "h"}))
    (src / "Local State").write_text(json.dumps(
        {"os_crypt": {"encrypted_key": "K"},
         "user_experience_metrics": {"client_id": "CID"},
         "variations": "v"}))
    return src


def _use_tmp_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_GAVEL_BROWSER_PROFILES", str(tmp_path / "profiles"))


def test_copy_default_tier(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    res = profiles.copy_profile(source=str(src), name="t1", tier="default")
    dest = tmp_path / "profiles" / "t1"
    # 反识别项保留
    assert (dest / "Default" / "Cookies").exists()
    assert (dest / "Default" / "History").exists()
    assert (dest / "Default" / "Local Storage" / "leveldb" / "x.ldb").exists()
    # 隐私项与缓存剔除
    assert not (dest / "Default" / "Login Data").exists()
    assert not (dest / "Default" / "Sync Data").exists()
    assert not (dest / "Default" / "Cache").exists()
    assert not (dest / "Default" / "Extensions").exists()  # 扩展默认不复制
    # 清洗：保留 cookie 解密密钥，去掉设备标识
    ls = json.loads((dest / "Local State").read_text())
    assert ls["os_crypt"]["encrypted_key"] == "K"
    assert "user_experience_metrics" not in ls
    pref = json.loads((dest / "Default" / "Preferences").read_text())
    assert "sync" not in pref and pref["homepage"] == "h"
    # 第一个副本自动成为当前
    assert res["became_active"] is True and res["active"] == "t1"


def test_copy_strict_excludes_credentials(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    profiles.copy_profile(source=str(src), name="s1", tier="strict")
    dest = tmp_path / "profiles" / "s1"
    assert not (dest / "Default" / "Cookies").exists()
    assert not (dest / "Default" / "Local Storage").exists()
    assert (dest / "Default" / "History").exists()  # strict 仍保留历史


def test_copy_full_includes_extensions(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    profiles.copy_profile(source=str(src), name="f1", tier="full",
                          include_extensions=True)
    assert (tmp_path / "profiles" / "f1" / "Default" / "Extensions"
            / "abc" / "manifest.json").exists()


def test_copy_no_history(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    profiles.copy_profile(source=str(src), name="nh", tier="default",
                          include_history=False)
    assert not (tmp_path / "profiles" / "nh" / "Default" / "History").exists()


def test_active_switch_and_delete(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    profiles.copy_profile(source=str(src), name="a")
    profiles.copy_profile(source=str(src), name="b")
    assert profiles.active_name() == "a"  # 首个成为当前，第二个不改
    profiles.set_active("b")
    assert profiles.active_name() == "b"
    names = {p["name"] for p in profiles.list_profiles()}
    assert names == {"a", "b"}
    profiles.delete_profile("b")
    assert profiles.active_name() == "a"  # 删当前后回退到剩余


def test_bad_name_rejected(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    src = _make_source(tmp_path)
    from agent_gavel.runtime.errors import GavelError
    with pytest.raises(GavelError):
        profiles.copy_profile(source=str(src), name="../evil")


def test_missing_source(monkeypatch, tmp_path):
    _use_tmp_profiles(monkeypatch, tmp_path)
    from agent_gavel.runtime.errors import GavelError
    with pytest.raises(GavelError):
        profiles.copy_profile(source=str(tmp_path / "nope"), name="x")
