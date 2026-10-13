"""浏览器资料副本工具（docs/detection.md）。

把用户本机 Chrome 的资料目录复制成我们自己的副本，供 C1 自启浏览器使用；
可保存多份、在副本间切换。每次 browser_profile_copy = 从用户当前资料目录做一份新副本。
"""

import asyncio

from ..runtime import profiles
from .common import guard


def register_profile_tools(mcp):
    @mcp.tool()
    @guard
    async def browser_profile_copy(source: str = None, name: str = None,
                                   tier: str = "default",
                                   include_history: bool = True,
                                   include_extensions: bool = False,
                                   force: bool = False):
        """从用户 Chrome 资料目录复制一份新副本。

        tier:
          strict  = 不含 cookie 与站点本地态（隐私最好，拿不到已登录/回头客）
          default = 含 cookie/站点态/历史（推荐，反识别优先）
          full    = default + 扩展代码
        始终剔除：密码/自动填充/同步/设备标识/各类缓存。
        若还没有任何副本，本次复制会自动成为当前使用的副本；否则只新增副本。
        源浏览器必须没在跑（否则报 profile_source_locked；force 可强行但可能不一致）。
        """
        return await asyncio.to_thread(
            profiles.copy_profile, source=source, name=name, tier=tier,
            include_history=include_history,
            include_extensions=include_extensions, force=force)

    @mcp.tool()
    @guard
    async def browser_profile_list():
        """列出所有资料副本 + 哪个是当前使用的。"""
        return {"status": "ok", "active": profiles.active_name(),
                "items": profiles.list_profiles()}

    @mcp.tool()
    @guard
    async def browser_profile_use(name: str):
        """切换当前使用的资料副本（下次 C1 启动浏览器时生效）。"""
        items = {p["name"] for p in profiles.list_profiles()}
        if name not in items:
            return {"status": "error", "reason": "profile_not_found",
                    "error": f"副本不存在：{name}", "items": sorted(items)}
        profiles.set_active(name)
        return {"status": "ok", "active": name}

    @mcp.tool()
    @guard
    async def browser_profile_delete(name: str):
        """删除一个资料副本（不影响用户原目录）。"""
        return await asyncio.to_thread(profiles.delete_profile, name)
