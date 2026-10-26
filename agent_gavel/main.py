"""MCP stdio 适配器入口。

真正的浏览器与运行时在 daemon 进程里；本进程只负责把工具调用转发过去。
因此 adapter 不导入 Playwright、不持有任何运行时状态。
"""

import os
import signal
import sys
import threading
import time

from . import remote

_PARENT_PID = os.getppid()


def _set_pdeathsig():
    """父进程（opencode）退出时让 adapter 一起退出，避免残留。"""
    if os.name == "nt" or not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)
    except Exception:
        pass  # silent-ok: best-effort，失败不影响主流程


def _start_parent_watchdog():
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
                    os._exit(0)
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    threading.Thread(target=_watch, daemon=True).start()


def main():
    _set_pdeathsig()
    _start_parent_watchdog()
    import asyncio

    try:
        asyncio.run(remote.run())
    except Exception as e:  # noqa: BLE001
        print(f"agent-gavel: {e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
