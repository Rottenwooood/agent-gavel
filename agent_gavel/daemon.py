"""单实例运行时守护进程。

所有浏览器、session/context/page、队列、事件与产物只存在于本进程。
MCP 适配器（main.py）通过本地 socket 转发工具调用，不再各自持有运行时。

单实例由 <数据根>/daemon.lock 的 flock 保证：内核在进程崩溃时自动释放，
不依赖 pid 判断。
"""

import asyncio
import base64
import errno
import json
import os
import secrets
import signal
import sys

from .core import config
from .core.errors import wrap_exception
from .core.logging import setup as setup_logging
from .core.results import TOOL_API_VERSION, ToolReply
from .tools import create_catalog


def _paths():
    root = config.data_dir()
    return {
        "lock": root / "daemon.lock",
        "sock": root / "daemon.sock",
        "info": root / "daemon.json",
    }


def _acquire_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EAGAIN, errno.EACCES):
            return None
        raise
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd


async def _dispatch(catalog, schemas, name, args, adapter_count):
    if name == "__describe__":
        return {"tools": schemas, "daemon_pid": os.getpid(), "tool_api_version": TOOL_API_VERSION}
    if name == "__ping__":
        return {"status": "ok", "daemon_pid": os.getpid()}
    arguments = args if args is not None else {}
    reply = await catalog.call(name, arguments)
    if (
        name == "browser"
        and isinstance(arguments, dict)
        and arguments.get("op") == "status"
        and arguments.get("detail") == "full"
    ):
        reply.result.update(daemon_pid=os.getpid(), adapters=adapter_count())
    return reply


async def _serve(paths, token, registry, schemas, stop):
    state = {"adapters": 0}
    tasks = set()
    writers = set()

    async def reply(req, writer, write_lock, adapter_count):
        rid = req.get("id")
        if req.get("token") != token:
            data = (
                json.dumps(
                    {
                        "id": rid,
                        "error": {
                            "status": "error",
                            "reason": "unauthorized",
                            "error": "daemon token 不匹配",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode()
            async with write_lock:
                writer.write(data)
                try:
                    await writer.drain()
                except Exception:
                    pass  # silent-ok: best-effort，失败不影响主流程
            return
        try:
            result = await _dispatch(
                registry, schemas, req.get("tool"), req.get("args"), adapter_count
            )
            if isinstance(result, ToolReply):
                out = {
                    "id": rid,
                    "result": result.result,
                    "is_error": result.result.get("status") == "error",
                }
                if result.images:
                    out["images"] = [
                        {
                            "type": "image",
                            "mimeType": image.mime_type,
                            "data": base64.b64encode(image.data).decode("ascii"),
                        }
                        for image in result.images
                    ]
            else:
                out = {"id": rid, "result": result}
        except Exception as e:  # noqa: BLE001
            out = {"id": rid, "error": wrap_exception(e).to_dict()}
        data = (json.dumps(out, ensure_ascii=False, default=str) + "\n").encode()
        async with write_lock:
            writer.write(data)
            try:
                await writer.drain()
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    def adapter_count():
        return state["adapters"]

    async def handle(reader, writer):
        state["adapters"] += 1
        writers.add(writer)
        write_lock = asyncio.Lock()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except Exception:
                    continue
                task = asyncio.create_task(reply(req, writer, write_lock, adapter_count))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            state["adapters"] -= 1
            writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass  # silent-ok: best-effort，失败不影响主流程

    transport = {}
    if os.name == "nt":
        server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=64 * 1024 * 1024)
        transport["port"] = server.sockets[0].getsockname()[1]
    else:
        sock_path = paths["sock"]
        if await asyncio.to_thread(os.path.exists, sock_path):
            os.unlink(sock_path)
        server = await asyncio.start_unix_server(
            handle, path=str(sock_path), limit=64 * 1024 * 1024
        )
        os.chmod(sock_path, 0o600)
        transport["socket"] = str(sock_path)
    paths["info"].write_text(
        json.dumps({"pid": os.getpid(), "token": token, **transport}), encoding="utf-8"
    )
    try:
        os.chmod(paths["info"], 0o600)
    except OSError:
        pass
    await stop.wait()

    server.close()
    # 主动断开 adapter，否则 wait_closed 会一直等现存连接
    for writer in list(writers):
        try:
            writer.close()
        except Exception:
            pass  # silent-ok: 单条连接关闭失败不影响整体退出
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=2)
    except Exception:
        pass  # silent-ok: 已有连接关闭失败不影响退出
    for task in list(tasks):
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if os.name != "nt":
        try:
            os.unlink(paths["sock"])
        except OSError:
            pass


async def _run(lock_fd):
    paths = _paths()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    registry = create_catalog()
    schemas = registry.schemas()
    token = secrets.token_hex(16)

    from .runtime import get_runtime

    runtime = get_runtime()
    runtime.shutdown_hook = lambda: loop.call_later(0.5, stop.set)

    try:
        await _serve(paths, token, registry, schemas, stop)
    finally:
        try:
            from .runtime import shutdown_runtime

            await asyncio.wait_for(shutdown_runtime(), timeout=8)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        try:
            paths["info"].unlink()
        except OSError:
            pass
        try:
            os.close(lock_fd)
        except OSError:
            pass


def main():
    setup_logging()
    # daemon 必须比拉起它的 adapter 活得久：显式清掉可能继承的父进程死亡信号。
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").prctl(1, 0, 0, 0, 0)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
    lock_fd = _acquire_lock(_paths()["lock"])
    if lock_fd is None:
        return
    asyncio.run(_run(lock_fd))


if __name__ == "__main__":
    main()
