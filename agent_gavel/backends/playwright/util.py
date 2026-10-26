"""后端内部小工具。"""

import asyncio


async def sleep(seconds):
    await asyncio.sleep(seconds)
