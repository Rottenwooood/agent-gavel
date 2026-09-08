import asyncio
import ctypes
import os
import signal
import threading

from server import mcp
from adapter import close_resident


def _set_pdeathsig():
    """Linux PR_SET_PDEATHSIG：父进程(opencode)一死，本进程立刻收到 SIGTERM。

    防止 opencode 被 SIGKILL/崩溃时 agent-gavel 及其 spawn 的
    computer-use-linux 子进程残留成孤儿（opencode 强杀不会关我们的 stdin，
    anyio 的 stdio 会一直阻塞，靠 EOF 退出不可靠）。
    这是内核级保证：不依赖父进程被回收、不依赖轮询。
    """
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG=1


def _shutdown(signum, frame):  # noqa: ARG001
    """SIGTERM（父进程死亡）时：先清理常驻 cul 子进程，再退出。"""
    try:
        # close_resident 是 async；在信号 handler 里用独立线程跑事件循环。
        threading.Thread(target=_close_resident_sync, daemon=True).start()
    except Exception:
        pass
    os._exit(0)


def _close_resident_sync():
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(close_resident())
        loop.close()
    except Exception:
        pass


# 父进程死亡信号：在主线程注册（必须在 fork 后、单线程状态时设置）
signal.signal(signal.SIGTERM, _shutdown)
_set_pdeathsig()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
