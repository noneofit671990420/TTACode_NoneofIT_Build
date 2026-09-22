"""MCP (Model Context Protocol) client — skeleton.

Configuration format (JSON, e.g. ``~/.ttacode/mcp.json``)::

    {"servers": [
        {"name": "filesystem", "command": ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"], "args": []},
        {"name": "remote", "url": "http://127.0.0.1:9000/sse"}
    ]}

Only stdio (``command``) and SSE (``url``) transports are planned.
See ``docs/mcp-roadmap.md`` for the implementation plan.
"""

from .client import MCPServerConfig, load_servers

__all__ = ["MCPServerConfig", "load_servers"]
