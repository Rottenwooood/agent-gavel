import asyncio
import ctypes
import os
import signal
import threading

from mcp.server.mcpserver import MCPServer

from .channels.desktop.server import register_desktop_tools
from .channels.dom.server import register_dom_tools

mcp = MCPServer("agent-gavel")
register_dom_tools(mcp)
register_desktop_tools(mcp)


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
    """SIGTERM（父进程死亡）时：先清理常驻 cul 子进程 + 自管 Chrome，再退出。"""
    try:
        # close_resident 是 async；在信号 handler 里用独立线程跑事件循环。
        threading.Thread(target=_cleanup_sync, daemon=True).start()
    except Exception:
        pass
    os._exit(0)


def _cleanup_sync():
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(asyncio.gather(
            close_resident(),
            asyncio.to_thread(_stop_owned_chrome),
        ))
        loop.close()
    except Exception:
        pass


def close_resident():
    """清理 desktop adapter 的常驻 computer-use-linux 进程。"""
    from .channels.desktop.adapter import close_resident as _cr
    return _cr()


def _stop_owned_chrome():
    try:
        from .browser_manager import stop_own
        stop_own()
    except Exception:
        pass


# 父进程死亡信号：在主线程注册（必须在 fork 后、单线程状态时设置）
signal.signal(signal.SIGTERM, _shutdown)
_set_pdeathsig()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
