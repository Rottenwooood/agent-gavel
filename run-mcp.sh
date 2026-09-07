#!/usr/bin/env bash
# agent-claw MCP server 启动器：opencode 通过这个脚本拉起 server
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python3 main.py
