"""浏览器资料目录工具（docs/detection.md）。

资料目录按名字寻址：`profiles/<name>/`，默认名 `primary`。
没有 ACTIVE 指针——不传名字永远落 primary，多环境靠显式名字。
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

    @catalog.operation(
        returns="ProfileCopied", fields={"tier": {"enum": ["strict", "default", "full"]}}
    )
    async def browser_profile_ensure(
        source: str = None,
        name: str = None,
        tier: str = "default",
        include_history: bool = True,
        include_extensions: bool = False,
        force: bool = False,
    ):
        "幂等：目录已存在就直接返回，不做复制。"
        return await asyncio.to_thread(
            profiles.ensure_profile,
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
            "default": profiles.DEFAULT_PROFILE,
            "items": await asyncio.to_thread(profiles.list_profiles),
        }

    @catalog.operation(returns="ProfileDeleted")
    async def browser_profile_delete(name: str):
        "只删除资料目录，须明确授权。"
        return await asyncio.to_thread(profiles.delete_profile, name)
