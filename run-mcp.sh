#!/usr/bin/env bash
# agent-gavel MCP server 启动器：opencode 通过这个脚本拉起 server
set -euo pipefail
cd "$(dirname "$0")"

# 默认用优化版 computer-use-linux(连接缓存+doctor缓存+fast_app_filter)，
# 已实测 read_state ~620->235ms、act_and_verify 闭环 ~1.5s->0.65s。
# 二进制优先项目内 bin/(git 忽略, 本机构建), 否则 ~/.local/bin, 否则系统原版。
# 回退原版：COMPUTER_USE_LINUX_BIN=computer-use-linux AGENT_GAVEL_FAST_APP_FILTER=0
if [[ -z "${COMPUTER_USE_LINUX_BIN:-}" ]]; then
    if [[ -x "$(dirname "$0")/bin/computer-use-linux-fast" ]]; then
        export COMPUTER_USE_LINUX_BIN="$(dirname "$0")/bin/computer-use-linux-fast"
    elif [[ -x "$HOME/.local/bin/computer-use-linux-fast" ]]; then
        export COMPUTER_USE_LINUX_BIN="$HOME/.local/bin/computer-use-linux-fast"
    fi
fi
export AGENT_GAVEL_FAST_APP_FILTER="${AGENT_GAVEL_FAST_APP_FILTER:-1}"

exec uv run python3 main.py
