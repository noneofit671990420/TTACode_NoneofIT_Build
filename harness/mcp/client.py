"""MCP client skeleton — config loading only. No fake protocol behavior.

The actual JSON-RPC implementation (stdio subprocess framing + SSE event
stream) is planned in ``docs/mcp-roadmap.md``. :meth:`MCPClient.connect`
raises :exc:`NotImplementedError` honestly until then.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MCPServerConfig:
    """One MCP server entry."""

    name: str
    command: list[str] = field(default_factory=list)  # stdio transport
    args: list[str] = field(default_factory=list)
    url: str = ""  # SSE transport
    env: dict[str, str] = field(default_factory=dict)

    @property
    def transport(self) -> str:
        if self.command:
            return "stdio"
        if self.url:
            return "sse"
        return "unknown"

    def validate(self) -> None:
        if not self.name:
            raise ValueError("MCP server entry needs a name")
        if not self.command and not self.url:
            raise ValueError(
                f"MCP server {self.name!r} needs 'command' (stdio) or 'url' (SSE)"
            )
        if self.command and self.url:
            raise ValueError(
                f"MCP server {self.name!r} must use one transport, not both"
            )


def load_servers(config_path: str | Path) -> list[MCPServerConfig]:
    """Read MCP server configs from a JSON file.

    Missing file -> empty list (MCP is optional). Malformed JSON or
    invalid entries raise ValueError with a clear message.
    """
    path = Path(config_path).expanduser()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read MCP config {path}: {exc}") from exc
    if isinstance(data, dict):
        entries = data.get("servers", [])
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError(f"MCP config {path} must be an object or a list")
    servers: list[MCPServerConfig] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid MCP server entry (not an object): {entry!r}")
        server = MCPServerConfig(
            name=str(entry.get("name", "")),
            command=[str(c) for c in entry.get("command", []) or []],
            args=[str(a) for a in entry.get("args", []) or []],
            url=str(entry.get("url", "") or ""),
            env={str(k): str(v) for k, v in (entry.get("env", {}) or {}).items()},
        )
        server.validate()
        servers.append(server)
    return servers


class MCPClient:
    """Planned JSON-RPC client for one MCP server. Not yet implemented."""

    def __init__(self, config: MCPServerConfig) -> None:
        config.validate()
        self.config = config

    def connect(self) -> None:
        raise NotImplementedError(
            "MCP client transport is not yet implemented — "
            "see docs/mcp-roadmap.md for the planned stdio/SSE JSON-RPC work."
        )

    def list_tools(self) -> list[dict]:
        raise NotImplementedError(
            "MCP client transport is not yet implemented — "
            "see docs/mcp-roadmap.md."
        )

    def call_tool(self, name: str, arguments: dict):
        raise NotImplementedError(
            "MCP client transport is not yet implemented — "
            "see docs/mcp-roadmap.md."
        )
