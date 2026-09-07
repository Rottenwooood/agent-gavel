"""稳定等待（async 版）：轮询抓状态，直到连续 N 次一致或超时。

解决异步操作问题——点"发送"后网络请求要几百毫秒，单次立刻抓
会拿到过渡态。这里在工具内部等，不花模型调用。
"""

import time

from normalize import quick_hash


async def wait_until_stable(fetch_state, *, timeout_s=6.0, interval_s=0.3,
                            required_consecutive=2, seed_hash=None,
                            seed_data=None):
    """fetch_state: async 无参可调用，返回 state 原始数据（会被归一化比较）。

    seed_hash/seed_data: 若调用方已抓过 before，可传入作为第 0 次观察，
    避免"动作后还要先白等一次才能对比"，省一次 fetch 和一次间隔。
    返回 (stable, poll_count, elapsed_ms, final_hash, final_data)
    """
    start = time.monotonic()
    poll_count = 0
    last_hash = seed_hash
    consecutive = 1 if seed_hash is not None else 0
    last_data = seed_data

    while True:
        elapsed = time.monotonic() - start
        # 只有在已经有 seed 且没超时时才先 sleep，否则立即抓
        if poll_count > 0:
            await asyncio_sleep(interval_s)
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
        last_data = data

        elapsed = time.monotonic() - start
        if consecutive >= required_consecutive and last_hash is not None:
            return True, poll_count, int(elapsed * 1000), last_hash, last_data
        if elapsed >= timeout_s:
            return False, poll_count, int(elapsed * 1000), last_hash, last_data


async def asyncio_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
