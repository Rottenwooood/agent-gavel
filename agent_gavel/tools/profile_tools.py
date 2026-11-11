"""浏览器资料副本工具（docs/detection.md）。

把用户本机 Chrome 的资料目录复制成我们自己的副本，供 C1 自启浏览器使用；
可保存多份、在副本间切换。每次 browser_profile_copy = 从用户当前资料目录做一份新副本。
"""

import asyncio

from ..runtime import profiles


def register_profile_tools(catalog):
    @catalog.operation(
        returns="ProfileCopied", fields={"tier": {"enum": ["strict", "default", "full"]}}
    )
    async def browser_profile_copy(
        source: str = None,
        name: str = None,
        tier: str = "default",
        include_history: bool = True,
        include_extensions: bool = False,
        force: bool = False,
    ):
        "strict无登录态/default含站点态历史/full加扩展；源需关闭，force可不一致。"
        return await asyncio.to_thread(
            profiles.copy_profile,
            source=source,
            name=name,
            tier=tier,
            include_history=include_history,
            include_extensions=include_extensions,
            force=force,
        )

    @catalog.operation(returns="Profiles")
    async def browser_profile_list():
        """"""
        return {
            "status": "ok",
            "active": profiles.active_name(),
            "items": await asyncio.to_thread(profiles.list_profiles),
        }

    @catalog.operation(returns="ProfileActive")
    async def browser_profile_use(name: str):
        "须确认；下次启动生效。"
        items = {p["name"] for p in profiles.list_profiles()}
        if name not in items:
            return {
                "status": "error",
                "reason": "profile_not_found",
                "error": f"副本不存在：{name}",
                "items": sorted(items),
            }
        profiles.set_active(name)
        return {"status": "ok", "active": name}

    @catalog.operation(returns="ProfileDeleted")
    async def browser_profile_delete(name: str):
        "只删除副本，须明确授权。"
        return await asyncio.to_thread(profiles.delete_profile, name)
