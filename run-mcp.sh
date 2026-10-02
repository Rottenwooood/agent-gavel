#!/usr/bin/env bash
# agent-gavel MCP server 启动器：opencode 等 MCP 客户端通过这个脚本拉起 server
set -euo pipefail
cd "$(dirname "$0")"

# 只启动新 Playwright runtime。旧 DOM/Desktop 通道已归档到 legacy/，不运行。
exec uv run python3 -m agent_gavel.main
