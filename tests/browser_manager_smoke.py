"""browser_manager 冒烟回归：生命周期 / 外部隔离 / 崩溃自愈 / 无头兜底。

用法: cd agent-gavel && uv run python3 tests/browser_manager_smoke.py
会真实拉起/杀掉几次 Chrome(独立 profile /tmp/agent-gavel-chrome)，
结束后清理。失败抛 AssertionError。
"""

import asyncio
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_manager as bm  # noqa: E402
from dom_adapter import DomClient  # noqa: E402


def _step(name):
    print(f"\n== {name} ==", flush=True)


async def _nav():
    async with DomClient() as c:
        await c.navigate("https://example.com")
        return await c.get_title()


def test_lifecycle():
    _step("1. 未启动 status")
    s = bm.status()
    assert s["owner"] == "none", s

    _step("2. ensure 首次自启")
    r = bm.ensure_chrome(timeout_s=25)
    assert r["ok"] and r["owner"] == "started", r

    _step("3. 幂等(不重拉)")
    r = bm.ensure_chrome()
    assert r["owner"] == "self", r
    pid = bm._read_pidfile()

    _step("4. 停自己")
    r = bm.stop_own()
    assert r["stopped"] and r["pid"] == pid, r
    time.sleep(0.5)
    assert bm.status()["owner"] == "none"

    _step("5. 停无主安全")
    r = bm.stop_own()
    assert not r["stopped"]


def test_external_not_killed():
    _step("6. 外部 Chrome 不误杀")
    bm.ensure_chrome(timeout_s=25)
    bm.stop_own()
    time.sleep(0.5)
    ext = subprocess.Popen(
        [bm.chrome_binary(), "--no-sandbox", "--disable-gpu", "--no-first-run",
         "--headless=new",
         f"--user-data-dir={bm.USER_DATA_DIR}-ext",
         f"--remote-debugging-port={bm.DEBUG_PORT}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if bm.status()["owner"] == "external":
                break
            time.sleep(0.5)
        assert bm.status()["owner"] == "external", bm.status()
        assert not bm.stop_own()["stopped"]  # 无 pidfile 记录, 不杀
        assert ext.poll() is None, "外部 Chrome 被误杀!"
        print("   外部 chrome 存活 ✓")
    finally:
        ext.kill()
        ext.wait()
        time.sleep(0.5)


def test_crash_recover():
    _step("7. 崩溃自愈")
    bm.ensure_chrome(timeout_s=25)
    pid = bm._read_pidfile()
    os.killpg(pid, signal.SIGKILL)
    time.sleep(1)
    assert bm.status()["owner"] == "none"
    # 下一次连接自动重拉
    asyncio.run(_nav())
    assert bm.status()["owner"] == "self"


def test_no_display_errors():
    _step("8. 无 DISPLAY 报错(永不用 headless)")
    for k in ("DISPLAY", "WAYLAND_DISPLAY"):
        os.environ.pop(k, None)
    bm.stop_own()
    r = bm.ensure_chrome()
    assert not r["ok"], r
    assert "DISPLAY" in r.get("error", ""), r


def main():
    test_lifecycle()
    test_external_not_killed()
    test_crash_recover()
    test_no_display_errors()
    _step("清理")
    bm.stop_own()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
