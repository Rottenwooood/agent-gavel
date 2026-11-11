"""Transport-neutral result values; no MCP or browser imports."""

from dataclasses import dataclass, field

TOOL_API_VERSION = 2


@dataclass(frozen=True)
class Image:
    data: bytes
    mime_type: str = "image/png"


@dataclass
class ToolReply:
    result: dict
    images: list[Image] = field(default_factory=list)
