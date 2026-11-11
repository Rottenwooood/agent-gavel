"""Compatibility filename for the black-box MCP acceptance command."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("e2e_mcp.py")), run_name="__main__")
