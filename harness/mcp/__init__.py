"""MCP (Model Context Protocol) client — real JSON-RPC 2.0 implementation.

Configuration format (JSON, e.g. ``~/.ttacode/mcp.json``)::

    {"servers": [
        {"name": "filesystem", "command": ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"]},
        {"name": "remote", "url": "http://127.0.0.1:9000/mcp"}
    ]}

Transports: stdio (``command``) and HTTP (``url`` — streamable HTTP,
with best-effort fallback for legacy SSE servers). See
``docs/mcp-roadmap.md`` for status and ``docs/mcp.md`` for setup examples.
"""

from .bridge import MCPBridge, check_server, mcp_tool_name, mount_mcp_tools
from .client import (
    LATEST_PROTOCOL_VERSION,
    MCPClient,
    MCPError,
    MCPServerConfig,
    LegacySseTransport,
    StdioTransport,
    StreamableHttpTransport,
    load_servers,
)

__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "MCPBridge",
    "MCPClient",
    "MCPError",
    "MCPServerConfig",
    "LegacySseTransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "check_server",
    "load_servers",
    "mcp_tool_name",
    "mount_mcp_tools",
]
