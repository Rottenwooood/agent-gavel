"""策略与安全边界（docs/tech-plan.md §5.10）。

- domain allowlist：非空时导航/顶层请求越界 → policy_blocked
- file allowlist：artifact_export 目标目录、上传源文件必须在内
- 下载大小上限、artifact TTL/配额
- sensitive_params 脱敏由调用方配合（见 tools）
- 副作用审批 requires_confirmation 由 runner/工具层执行

环境变量：
  AGENT_GAVEL_ALLOW_DOMAINS      逗号分隔域名；空 = 不限制（开发默认）
  AGENT_GAVEL_ALLOW_PATHS        逗号分隔允许的本地目录
  AGENT_GAVEL_MAX_DOWNLOAD_BYTES 单文件下载上限（默认 256MB）
  AGENT_GAVEL_ARTIFACT_TTL_DAYS  artifact 保留天数（默认 7）
  AGENT_GAVEL_ARTIFACT_MAX_BYTES artifact 总量上限（默认 2GB）
"""

import os
from urllib.parse import urlparse

from .errors import GavelError

_DEFAULT_MAX_DOWNLOAD = 256 * 1024 * 1024
_DEFAULT_ARTIFACT_TTL_DAYS = 7
_DEFAULT_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024 * 1024


def _env_set(name):
    raw = (os.environ.get(name) or "").strip()
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


class PolicyManager:
    def __init__(self):
        self.domains = _env_set("AGENT_GAVEL_ALLOW_DOMAINS")
        # 发布/Docker 加固：即使 allowlist 为空也强制开启域名限制（空集=全拒，
        # 必须显式配置 AGENT_GAVEL_ALLOW_DOMAINS 才放行）。开发默认关。
        self.domain_guard = (os.environ.get("AGENT_GAVEL_DOMAIN_GUARD") or "").strip().lower() \
            in ("1", "true", "yes", "on")
        self.allowed_paths = [p for p in
                              (os.environ.get("AGENT_GAVEL_ALLOW_PATHS") or "").split(",")
                              if p.strip()]
        self.max_download_bytes = _env_int("AGENT_GAVEL_MAX_DOWNLOAD_BYTES",
                                           _DEFAULT_MAX_DOWNLOAD)
        self.artifact_ttl_days = _env_int("AGENT_GAVEL_ARTIFACT_TTL_DAYS",
                                          _DEFAULT_ARTIFACT_TTL_DAYS)
        self.artifact_max_bytes = _env_int("AGENT_GAVEL_ARTIFACT_MAX_BYTES",
                                           _DEFAULT_ARTIFACT_MAX_BYTES)

    @property
    def domain_restricted(self) -> bool:
        return bool(self.domains) or self.domain_guard

    def is_domain_allowed(self, url) -> bool:
        """判断 url 主机是否在允许范围。

        - 开发默认：allowlist 为空且未加固 → 全放行。
        - allowlist 非空 → 只放行命中项。
        - 加固且 allowlist 为空 → 全拒（保守空集）。
        """
        if not self.domain_restricted or not url:
            return True
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return True
        for d in self.domains:
            if host == d or host.endswith("." + d):
                return True
        return False

    def check_domain(self, url):
        """非空 allowlist 时校验 url 主机；越界抛 policy_blocked。"""
        if self.is_domain_allowed(url):
            return
        host = (urlparse(url).hostname or "").lower()
        raise GavelError(
            "policy_blocked",
            f"域名不在白名单：{host}",
            detail={"host": host, "allowed": sorted(self.domains)},
            hint="若确需访问，让用户把该域名加入 AGENT_GAVEL_ALLOW_DOMAINS",
        )

    def check_local_path(self, path, *, purpose="访问"):
        """空 allowlist = 不限制；非空则路径必须在允许目录内。"""
        if not self.allowed_paths or not path:
            return
        real = os.path.realpath(path)
        for root in self.allowed_paths:
            rroot = os.path.realpath(root)
            if real == rroot or real.startswith(rroot + os.sep):
                return
        raise GavelError(
            "policy_blocked",
            f"{purpose}的路径不在允许目录内：{path}",
            detail={"allowed": self.allowed_paths},
            hint="让用户把目录加入 AGENT_GAVEL_ALLOW_PATHS",
        )

    def check_download_size(self, size_bytes):
        if size_bytes is not None and size_bytes > self.max_download_bytes:
            raise GavelError(
                "download_too_large",
                f"下载大小 {size_bytes} 超过上限 {self.max_download_bytes}",
                retryable=False,
            )
