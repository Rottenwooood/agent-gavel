"""agent-gavel 新运行时（Playwright 内核）。见 docs/tech-plan.md。"""

from ..core.errors import GavelError, wrap_exception
from .browser_runtime import BrowserRuntime, get_runtime, reset_runtime, shutdown_runtime

__all__ = [
    "BrowserRuntime",
    "get_runtime",
    "reset_runtime",
    "shutdown_runtime",
    "GavelError",
    "wrap_exception",
]
