import asyncio
import os
import signal
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer

from .tools import register_runtime_tools

mcp = MCPServer("agent-gavel")
# 只注册新 Playwright runtime 工具。旧的 DOM(裸 CDP) / Desktop(AT-SPI) 通道已
# 移入仓库根 legacy/ 目录，不打包、不注册、不运行（见 legacy/README.md）。
register_runtime_tools(mcp)

# 记录启动时父进程 pid，供 Windows 看门狗使用。
_PARENT_PID = os.getppid()


def _set_pdeathsig():
    """Linux PR_SET_PDEATHSIG：父进程(opencode)一死，本进程立刻收到 SIGTERM。

    防止 opencode 被 SIGKILL/崩溃时 agent-gavel 及其 spawn 的
    computer-use-linux 子进程残留成孤儿（opencode 强杀不会关我们的 stdin，
    anyio 的 stdio 会一直阻塞，靠 EOF 退出不可靠）。
    这是内核级保证：不依赖父进程被回收、不依赖轮询。
    Windows 无 PR_SET_PDEATHSIG，改用 psutil 父进程看门狗（见 _start_parent_watchdog）。
    """
    if os.name == "nt":
        return
    try:
        import ctypes
        if sys.platform.startswith("linux"):
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG=1
    except Exception:
        pass


def _shutdown(signum, frame):  # noqa: ARG001
    """退出信号：先清理新 runtime 的浏览器进程，再退出。"""
    try:
        threading.Thread(target=_cleanup_sync, daemon=True).start()
    except Exception:
        pass
    os._exit(0)


async def _shutdown_runtime():
    """尽力关闭新 runtime 的浏览器进程（跨事件循环失败也无妨）。"""
    try:
        from .runtime import shutdown_runtime
        await shutdown_runtime()
    except Exception:
        pass


def _cleanup_sync():
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(asyncio.gather(_shutdown_runtime()))
        loop.close()
    except Exception:
        pass


def _start_parent_watchdog():
    """Windows 模拟 PR_SET_PDEATHSIG：轮询父进程存活，死了就清理并退出。"""
    if os.name != "nt":
        return
    try:
        import psutil
    except Exception:
        return

    def _watch():
        while True:
            time.sleep(1.0)
            try:
                if not psutil.pid_exists(_PARENT_PID):
                    _cleanup_sync()
                    os._exit(0)
            except Exception:
                pass

    threading.Thread(target=_watch, daemon=True).start()


def _register_exit_signals():
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError, RuntimeError):
            pass


_register_exit_signals()
_set_pdeathsig()
_start_parent_watchdog()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
