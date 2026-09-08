#!/usr/bin/env bash
# agent-gavel MCP server 启动器：opencode 通过这个脚本拉起 server
set -euo pipefail
cd "$(dirname "$0")"

# 默认用优化版 computer-use-linux(连接缓存+doctor缓存+fast_app_filter)，
# 已实测 read_state ~620->235ms、act_and_verify 闭环 ~1.5s->0.65s。
# 回退原版：COMPUTER_USE_LINUX_BIN=computer-use-linux AGENT_GAVEL_FAST_APP_FILTER=0
export COMPUTER_USE_LINUX_BIN="${COMPUTER_USE_LINUX_BIN:-$HOME/.local/bin/computer-use-linux-fast}"
export AGENT_GAVEL_FAST_APP_FILTER="${AGENT_GAVEL_FAST_APP_FILTER:-1}"

exec uv run python3 main.py
