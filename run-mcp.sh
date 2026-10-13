#!/usr/bin/env bash
# agent-gavel MCP server 启动器：opencode 等 MCP 客户端通过这个脚本拉起 server
set -euo pipefail
cd "$(dirname "$0")"

# 只启动新 Playwright runtime。旧 DOM/Desktop 通道已归档到 legacy/，不运行。

# 有头模式下确保 DISPLAY 可用：MCP 客户端可能在显示器变化前启动，会继承到已失效的
# DISPLAY，导致 Chrome 起不来。这里校验 X socket，失效则自动选一个可用的。
if [[ "${AGENT_GAVEL_HEADLESS:-0}" != "1" ]]; then
    if [[ -z "${DISPLAY:-}" || ! -S "/tmp/.X11-unix/X${DISPLAY#:}" ]]; then
        for s in /tmp/.X11-unix/X*; do
            if [[ -S "$s" ]]; then
                export DISPLAY=":${s##*X}"
                break
            fi
        done
    fi
fi

exec uv run python3 -m agent_gavel.main
