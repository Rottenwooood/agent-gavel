"""稳定等待（async 版）：轮询抓状态，直到连续 N 次一致或超时。

解决异步操作问题——点"发送"后网络请求要几百毫秒，单次立刻抓
会拿到过渡态。这里在工具内部等，不花模型调用。
"""

import time

from normalize import quick_hash


async def wait_until_stable(fetch_state, *, timeout_s=8.0, interval_s=0.5,
                            required_consecutive=2):
    """fetch_state: async 无参可调用，返回 state 原始数据（会被归一化比较）。

    返回 (stable: bool, poll_count: int, elapsed_ms: int, final_hash: str)
    """
    start = time.monotonic()
    poll_count = 0
    last_hash = None
    consecutive = 0

    while True:
        try:
            data = await fetch_state()
        except Exception:
            data = None
        h = quick_hash(data) if data is not None else None
        poll_count += 1

        if h is not None and h == last_hash:
            consecutive += 1
        else:
            consecutive = 1
            last_hash = h

        elapsed = time.monotonic() - start
        if consecutive >= required_consecutive and last_hash is not None:
            return True, poll_count, int(elapsed * 1000), last_hash
        if elapsed >= timeout_s:
            return False, poll_count, int(elapsed * 1000), last_hash

        await asyncio_sleep(interval_s)


async def asyncio_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
