"""唯一的环境变量与路径入口。

约定：
- 全部是函数，惰性读取；测试改环境变量后立即生效。
- 空字符串一律视为未设置（`os.environ.get(k) or default`）。
- 其他模块不得直接读 os.environ。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

_TRUE = ("1", "true", "yes", "on")

_CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _flag(name):
    return (_env(name) or "").strip().lower() in _TRUE


# ---------------- 目录 ----------------


def data_dir() -> Path:
    return Path(_env("AGENT_GAVEL_DATA_DIR") or Path.home() / ".config" / "agent-gavel")


def idle_exit() -> float:
    """没有任何 adapter 连接多久后 daemon 自行退出（秒）。0 表示不退出。"""
    raw = _env("AGENT_GAVEL_IDLE_EXIT")
    if raw is None:
        return 120.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 120.0


def profile_dir() -> Path:
    """独立 session 使用的资料目录根。"""
    return Path(_env("AGENT_GAVEL_PROFILE_DIR") or data_dir() / "profiles")


def browser_profiles_root() -> Path:
    """资料副本存放目录。"""
    return Path(_env("AGENT_GAVEL_BROWSER_PROFILES") or data_dir() / "browser-profiles")


def default_browser_profile() -> Path:
    """没有任何副本时，C1 使用的可累计资料目录。"""
    return Path(_env("AGENT_GAVEL_BROWSER_PROFILE") or data_dir() / "browser-profile")


def browser_profile_override():
    """显式指定的资料目录（优先于当前副本）。"""
    return _env("AGENT_GAVEL_BROWSER_PROFILE")


def workflows_dir() -> Path:
    override = _env("AGENT_GAVEL_WORKFLOWS_DIR")
    if override:
        return Path(override)
    # 不要改 XDG_CONFIG_HOME 来隔离模板：Chrome 会读它，指向空目录会导致
    # 后续外网导航 net::ERR_NETWORK_CHANGED / 超时（已实测）。
    base = _env("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "agent-gavel" / "workflows"


def artifacts_dir() -> Path:
    return Path(
        _env("AGENT_GAVEL_ARTIFACT_DIR")
        or Path(tempfile.gettempdir()) / "agent-gavel" / "artifacts"
    )


# ---------------- 浏览器 ----------------


def headless() -> bool:
    return _flag("AGENT_GAVEL_HEADLESS")


def browser_mode() -> str:
    return (_env("AGENT_GAVEL_BROWSER_MODE") or "managed").strip().lower()


def cdp_endpoint():
    return _env("AGENT_GAVEL_CDP_ENDPOINT")


def extra_args():
    return (_env("AGENT_GAVEL_EXTRA_ARGS") or "").strip()


def disable_gpu() -> bool:
    return _flag("AGENT_GAVEL_DISABLE_GPU")


def chrome_bin():
    """managed 模式的可执行文件；找不到返回 None。"""
    configured = _env("AGENT_GAVEL_CHROME_BIN")
    if configured and os.path.isfile(configured):
        return configured
    if sys.platform.startswith("win"):
        candidates = []
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = _env(name)
            if base:
                candidates.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                )
        candidates += [shutil.which(n) for n in ("chrome.exe", "msedge.exe")]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None
    for name in _CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def chrome_channel():
    """Playwright 模式的 channel：显式配置优先，否则按系统已装的浏览器推断。"""
    if "AGENT_GAVEL_CHROME_CHANNEL" in os.environ:
        return os.environ["AGENT_GAVEL_CHROME_CHANNEL"].strip() or None
    names = (
        ("google-chrome", "google-chrome-stable")
        if not sys.platform.startswith("win")
        else ("chrome.exe",)
    )
    if any(shutil.which(n) for n in names):
        return "chrome"
    if any(shutil.which(n) for n in ("chromium", "chromium-browser", "chrome")):
        return "chromium"
    return None


def chrome_user_data_dir():
    """用户本机 Chrome 的资料目录（复制副本的来源）。"""
    override = _env("AGENT_GAVEL_CHROME_USER_DATA")
    if override:
        return override
    if sys.platform.startswith("win"):
        base = _env("LOCALAPPDATA") or ""
        return os.path.join(base, "Google", "Chrome", "User Data")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    for path in (
        os.path.expanduser("~/.config/google-chrome"),
        os.path.expanduser("~/.config/chromium"),
        os.path.expanduser("~/.config/google-chrome-beta"),
    ):
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~/.config/google-chrome")


# ---------------- 策略与配额 ----------------


def _csv(name):
    raw = (_env(name) or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def allow_domains() -> set:
    return {x.lower() for x in _csv("AGENT_GAVEL_ALLOW_DOMAINS")}


# 宿主（opencode）明确允许的临时目录；不配置白名单时无需追加
_DEFAULT_ALLOW_PATHS = ("/tmp/opencode",)


def allow_paths() -> list:
    paths = _csv("AGENT_GAVEL_ALLOW_PATHS")
    if not paths:
        return paths
    for extra in _DEFAULT_ALLOW_PATHS:
        if extra not in paths and os.path.isdir(extra):
            paths.append(extra)
    return paths


def domain_guard() -> bool:
    return _flag("AGENT_GAVEL_DOMAIN_GUARD")


def _int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def max_download_bytes() -> int:
    return _int("AGENT_GAVEL_MAX_DOWNLOAD_BYTES", 256 * 1024 * 1024)


def artifact_ttl_days() -> int:
    return _int("AGENT_GAVEL_ARTIFACT_TTL_DAYS", 7)


def artifact_max_bytes() -> int:
    return _int("AGENT_GAVEL_ARTIFACT_MAX_BYTES", 2 * 1024 * 1024 * 1024)
