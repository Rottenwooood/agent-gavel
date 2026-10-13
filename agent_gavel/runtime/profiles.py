"""浏览器资料副本管理（docs/detection.md）。

把用户本机 Chrome 的资料目录**复制**成我们自己的副本，用作 C1 自启浏览器的
用户资料目录。目的是拿到"像被用过"的身份（cookie/历史/站点态），同时：

- 只复制有反识别价值的项；剔除纯隐私项（密码/自动填充/同步/设备标识）与缓存；
- 副本由我们独占，绝不写回用户原目录，也不与其日常 Chrome 抢单实例；
- 每次调用 = 从用户当前资料目录做一份**新副本**；可保存多份、在副本间切换。

档位：
  strict  ：不含 cookie 与站点本地态（隐私最好，拿不到"已登录/回头客"）
  default ：含 cookie/站点态/历史等（反识别优先，推荐）
  full    ：default + 扩展代码
"""

import datetime
import json
import os
import shutil
import sys
import time

from .errors import GavelError

_HEADLESS_TRUE = ("1", "true", "yes", "on")


def _data_root():
    return os.environ.get("AGENT_GAVEL_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "agent-gavel")


def profiles_root():
    return os.environ.get("AGENT_GAVEL_BROWSER_PROFILES") or \
        os.path.join(_data_root(), "browser-profiles")


def default_profile_dir():
    """没有任何副本时，C1 用的自有可累计目录。"""
    return os.environ.get("AGENT_GAVEL_BROWSER_PROFILE") or \
        os.path.join(_data_root(), "browser-profile")


def _active_file():
    return os.path.join(profiles_root(), "ACTIVE")


def active_name():
    try:
        with open(_active_file(), encoding="utf-8") as f:
            return f.read().strip() or None
    except Exception:
        return None


def active_profile_dir():
    name = active_name()
    if not name:
        return None
    d = os.path.join(profiles_root(), name)
    return d if os.path.isdir(d) else None


def set_active(name):
    os.makedirs(profiles_root(), exist_ok=True)
    with open(_active_file(), "w", encoding="utf-8") as f:
        f.write(name)


def default_source():
    """用户本机 Chrome 的资料目录（可用 AGENT_GAVEL_CHROME_USER_DATA 覆盖）。"""
    override = os.environ.get("AGENT_GAVEL_CHROME_USER_DATA")
    if override:
        return override
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or ""
        return os.path.join(base, "Google", "Chrome", "User Data")
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Google/Chrome")
    for p in (os.path.expanduser("~/.config/google-chrome"),
              os.path.expanduser("~/.config/chromium"),
              os.path.expanduser("~/.config/google-chrome-beta")):
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~/.config/google-chrome")


# ---------------- 复制选择 ----------------

_CACHE_DIRS = {
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "Default/DawnCache", "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache", "Default/GrShaderCache",
    "Default/ShaderCache", "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache", "GraphiteDawnCache",
    "component_crx_cache", "extensions_crx_cache", "Crash Reports",
    "Crashpad", "Segmentation Platform", "Shared Dictionary",
    "optimization_guide_hint_cache_store", "Safe Browsing",
    "BrowserMetrics", "MEIPreload",
}
_CACHE_FILES = {"BrowserMetrics-spare.pma", "SingletonLock", "SingletonCookie",
                "SingletonSocket", "lockfile", "Local State.bak"}

_PRIVACY_FILES = {
    "Default/Login Data", "Default/Login Data-journal",
    "Default/Login Data For Account", "Default/Web Data",
    "Default/Web Data-journal", "Default/Account Web Data",
    "Default/Account Web Data-journal", "Default/Sync Data",
    "Default/Platform Notifications", "Default/GCM Store",
    "Default/Account Tracker", "Default/Visited Links",
    "Default/Media History", "Default/Media History-journal",
    "Default/Network/Trust Tokens", "Default/Network/Trust Tokens-journal",
    "Default/Network/Reporting and NEL", "Default/Network/SCT Auditing",
    "Default/Sessions", "Current Session", "Current Tabs",
    "Last Session", "Last Tabs",
}
_PRIVACY_PREFIX = (
    "Default/GCM Store", "Default/Platform Notifications",
    "Default/Sync Data", "Safe Browsing", "Default/Safe Browsing",
)

_CRED_DIRS = {
    "Default/Local Storage", "Default/IndexedDB", "Default/Service Worker",
    "Default/Session Storage", "Default/databases", "Default/File System",
    "Default/WebStorage", "Default/Shared Dictionary",
}
_CRED_FILES = {"Default/Cookies", "Default/Cookies-journal",
               "Default/Network/Cookies", "Default/Network/Cookies-journal"}
_HISTORY_FILES = {"Default/History", "Default/History-journal"}

_EXT_DIRS = {
    "Default/Extensions", "Default/Extension State",
    "Default/Local Extension Settings", "Default/Managed Extension Settings",
    "Default/Extension Rules", "Default/Extension Scripts",
    "Default/Extension Activity",
}

_TIERS = ("strict", "default", "full")


def _norm(p):
    return p.replace(os.sep, "/")


def _skip(rel, *, tier, include_history, include_extensions):
    rel = _norm(rel)
    top = rel.split("/")[0]
    if rel in _CACHE_DIRS or top in _CACHE_DIRS or rel in _CACHE_FILES:
        return True
    if rel in _PRIVACY_FILES:
        return True
    if any(rel.startswith(p) for p in _PRIVACY_PREFIX):
        return True
    if not include_history and (rel in _HISTORY_FILES or top == "Default/History"):
        return True
    if not include_extensions and rel in _EXT_DIRS:
        return True
    if tier == "strict":
        if rel in _CRED_FILES or rel.startswith("Default/Network/Cookies"):
            return True
        if rel in _CRED_DIRS:
            return True
    return False


def _sanitize_json(path, drop_keys):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        for k in list(data.keys()):
            if k in drop_keys:
                data.pop(k, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def copy_profile(*, source=None, name=None, tier="default",
                 include_history=True, include_extensions=False,
                 force=False) -> dict:
    """从用户资料目录复制一份新副本；无副本时自动设为当前。"""
    if tier not in _TIERS:
        raise GavelError("bad_args", f"tier 取 {list(_TIERS)}")
    source = source or default_source()
    if not os.path.isdir(source):
        raise GavelError("profile_source_missing",
                         f"用户资料目录不存在：{source}",
                         hint="用 AGENT_GAVEL_CHROME_USER_DATA 指定")
    if os.path.exists(os.path.join(source, "SingletonLock")) and not force:
        raise GavelError("profile_source_locked",
                         f"用户资料目录正被占用：{source}",
                         hint="先关掉用户的 Chrome 再复制（force 可强行，但可能不一致）")
    if name is None:
        name = "snap-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not _safe_name(name):
        raise GavelError("bad_args", f"副本名非法：{name!r}",
                         hint="只允许字母/数字/._-")
    root = profiles_root()
    dest = os.path.join(root, name)
    if os.path.exists(dest):
        raise GavelError("profile_exists", f"副本已存在：{name}",
                         hint="换一个 name，或先 browser_profile_delete")
    os.makedirs(dest, exist_ok=True)

    copied = skipped = 0
    for cur, dirs, files in os.walk(source):
        rel_dir = _norm(os.path.relpath(cur, source))
        rel_dir = "" if rel_dir == "." else rel_dir
        # 跳过被排除的目录
        keep = []
        for d in dirs:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if _skip(rel, tier=tier, include_history=include_history,
                     include_extensions=include_extensions):
                skipped += 1
            else:
                keep.append(d)
        dirs[:] = keep
        for fn in files:
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if _skip(rel, tier=tier, include_history=include_history,
                     include_extensions=include_extensions):
                skipped += 1
                continue
            src = os.path.join(cur, fn)
            dst = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(src, dst, follow_symlinks=False)
                copied += 1
            except Exception:
                skipped += 1

    # 清洗会绑定账号/设备的字段（保留 cookies 解密所需的 os_crypt）
    _sanitize_json(os.path.join(dest, "Local State"),
                   drop_keys={"user_experience_metrics", "uninstall_metrics",
                              "google.services", "signin", "sync", "gaia",
                              "account_info", "account_tracker_service_last_update"})
    _sanitize_json(os.path.join(dest, "Default", "Preferences"),
                   drop_keys={"account_info", "sync", "signin", "google"})
    _sanitize_json(os.path.join(dest, "Default", "Secure Preferences"),
                   drop_keys={"account_info", "sync", "signin", "google"})

    size = _dir_size(dest)
    manifest = {"name": name, "source": source, "created_at": time.time(),
                "tier": tier, "include_history": include_history,
                "include_extensions": include_extensions,
                "copied_files": copied, "skipped_files": skipped,
                "size_bytes": size}
    with open(os.path.join(dest, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    became_active = False
    if active_name() is None:
        set_active(name)
        became_active = True
    return {"status": "ok", "created": name, "became_active": became_active,
            "active": active_name(), "profiles": list_profiles(),
            "manifest": manifest}


def list_profiles() -> list:
    root = profiles_root()
    out = []
    if not os.path.isdir(root):
        return out
    active = active_name()
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        man = {}
        try:
            with open(os.path.join(p, "profile.json"), encoding="utf-8") as f:
                man = json.load(f)
        except Exception:
            man = {}
        out.append({"name": d, "active": d == active,
                    "tier": man.get("tier"), "created_at": man.get("created_at"),
                    "size_bytes": man.get("size_bytes") or _dir_size(p),
                    "source": man.get("source"),
                    "include_history": man.get("include_history"),
                    "include_extensions": man.get("include_extensions")})
    return out


def delete_profile(name) -> dict:
    if not _safe_name(name):
        raise GavelError("bad_args", f"副本名非法：{name!r}")
    p = os.path.join(profiles_root(), name)
    if not os.path.isdir(p):
        raise GavelError("profile_not_found", f"副本不存在：{name}")
    shutil.rmtree(p, ignore_errors=True)
    if active_name() == name:
        remaining = [x["name"] for x in list_profiles()]
        if remaining:
            set_active(remaining[0])
        else:
            try:
                os.remove(_active_file())
            except Exception:
                pass
    return {"status": "ok", "deleted": name, "active": active_name(),
            "profiles": list_profiles()}


def _safe_name(name) -> bool:
    if not isinstance(name, str) or not name:
        return False
    return all(c.isalnum() or c in "._-" for c in name)


def _dir_size(path):
    total = 0
    for cur, _d, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(cur, fn))
            except Exception:
                pass
    return total
