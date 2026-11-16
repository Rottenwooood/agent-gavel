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
    return Path(_env("AGENT_GAVEL_DATA_DIR") or user_data_root())


def user_data_root() -> Path:
    """跨平台的用户级数据根；不写死绝对路径。

    优先级：AGENT_GAVEL_DATA_DIR > 平台标准目录。
    - Windows：%LOCALAPPDATA%\\agent-gavel
    - macOS：~/Library/Application Support/agent-gavel
    - 其他：$XDG_DATA_HOME/agent-gavel 或 ~/.local/share/agent-gavel
    """
    override = _env("AGENT_GAVEL_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = _env("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "agent-gavel"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "agent-gavel"
    base = _env("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "agent-gavel"


def idle_exit() -> float:
    """没有任何 adapter 连接多久后 daemon 自行退出（秒）。0 表示不退出。"""
    raw = _env("AGENT_GAVEL_IDLE_EXIT")
    if raw is None:
        return 120.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 120.0


def browser_profiles_root() -> Path:
    """命名资料目录的根：profiles/<name>/。"""
    return Path(_env("AGENT_GAVEL_BROWSER_PROFILES") or user_data_root() / "profiles")


def default_browser_profile() -> Path:
    """默认资料目录：固定 <profiles 根>/primary，不受任何环境变量影响。"""
    return browser_profiles_root() / "primary"


def primary_headless_guard() -> bool:
    """主资料目录是否禁止无头启动；只有测试隔离模式豁免（CI 需要无头）。"""
    return not _flag("AGENT_GAVEL_TEST_ISOLATED")


def chrome_bus_env():
    """Linux 下给 Chrome 补 session bus；不需要补时返回 None。

    没有 session bus 就拿不到钥匙串密钥，v10/v11 cookie 解不开会被静默丢弃。
    """
    import sys  # noqa: PLC0415

    if not sys.platform.startswith("linux"):
        return None
    if _env("DBUS_SESSION_BUS_ADDRESS"):
        return None
    bus = f"/run/user/{os.getuid()}/bus"
    if not os.path.exists(bus):
        return None
    return {"DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}"}


def chrome_child_env():
    """Chrome 子进程环境：需要补 bus 时返回完整 env，否则 None（继承父进程）。"""
    bus = chrome_bus_env()
    if not bus:
        return None
    return {**os.environ, **bus}


def workflows_dir() -> Path:
    override = _env("AGENT_GAVEL_WORKFLOWS_DIR")
    if override:
        return Path(override)
    # 不要改 XDG_CONFIG_HOME 来隔离模板：Chrome 会读它，指向空目录会导致
    # 后续外网导航 net::ERR_NETWORK_CHANGED / 超时（已实测）。
    return user_data_root() / "workflows"


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
