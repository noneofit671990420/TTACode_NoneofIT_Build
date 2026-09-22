"""Bridge: mount live MCP servers as tools in the harness ToolRegistry.

Each configured server is connected, handshaked and asked for its tool
list; every MCP tool becomes a registry tool named
``mcp__<server>__<tool>`` with its description prefixed ``[mcp:<server>]``.
A dead or unreachable server is logged to stderr and skipped — one bad
server never breaks startup or the agent loop.

The returned :class:`MCPBridge` owns the live client connections; close
it when the run is over (the CLI does this in a ``finally`` block).
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

from .client import MCPClient, MCPError, MCPServerConfig, load_servers

# Registry names must be alnum/_/-; MCP tool names may contain dots etc.
_SANITIZE = re.compile(r"[^A-Za-z0-9_-]")


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Registry name for an MCP tool, sanitized for the registry rules."""
    base = f"mcp__{server_name}__{tool_name}"
    return _SANITIZE.sub("_", base)


def _function_schema(name: str, description: str, parameters: dict) -> dict:
    """Wrap an MCP inputSchema into the harness function-tool shape."""
    if not isinstance(parameters, dict):
        parameters = {"type": "object"}
    parameters = dict(parameters)
    parameters.setdefault("type", "object")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


class MCPBridge:
    """Owns live MCPClient connections mounted into a registry."""

    def __init__(self) -> None:
        self.clients: list[MCPClient] = []
        #: [{"server", "tool", "as"}] for every mounted tool.
        self.mounted: list[dict] = []
        #: [{"name", "error"}] for every server that could not be mounted.
        self.failed: list[dict] = []

    @property
    def tool_count(self) -> int:
        return len(self.mounted)

    @property
    def server_count(self) -> int:
        return len(self.clients)

    def close(self) -> None:
        clients, self.clients = self.clients, []
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def __enter__(self) -> "MCPBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def mount_mcp_tools(
    registry,
    ctx,
    config_path: str | Path | None = None,
    request_timeout: float = 30.0,
) -> MCPBridge:
    """Connect configured MCP servers and register their tools.

    ``config_path`` defaults to the CLI's ``~/.ttacode/mcp.json``.
    Never raises for server problems: failures are recorded on the
    returned bridge and warned about on stderr.
    """
    if config_path is None:
        from ..cli import mcp_config_path  # lazy: cli imports mcp lazily too

        config_path = mcp_config_path()
    bridge = MCPBridge()
    try:
        servers = load_servers(config_path)
    except ValueError as exc:
        _warn(f"ignoring MCP config ({exc})")
        return bridge
    for server in servers:
        client = MCPClient(server, request_timeout=request_timeout)
        try:
            client.connect()
        except Exception as exc:
            bridge.failed.append(
                {"name": server.name, "error": f"{type(exc).__name__}: {exc}"}
            )
            _warn(f"MCP server {server.name!r} unreachable — skipped ({exc})")
            continue
        try:
            tools = client.list_tools()
        except Exception as exc:
            bridge.failed.append(
                {"name": server.name, "error": f"{type(exc).__name__}: {exc}"}
            )
            _warn(f"MCP server {server.name!r}: tools/list failed — skipped ({exc})")
            try:
                client.close()
            except Exception:
                pass
            continue
        bridge.clients.append(client)
        for tool in tools:
            name = mcp_tool_name(server.name, tool["name"])
            description = f"[mcp:{server.name}] {tool['description']}".rstrip()
            schema = _function_schema(name, description, tool["schema"])

            def _handler(arguments, _client=client, _tool=tool["name"]):
                try:
                    return _client.call_tool(_tool, arguments)
                except MCPError as exc:
                    return {"ok": False, "error": str(exc)}

            try:
                registry.register(name, description, schema, _handler)
            except ValueError as exc:
                _warn(f"MCP tool {name!r} not registered ({exc})")
                continue
            bridge.mounted.append(
                {"server": server.name, "tool": tool["name"], "as": name}
            )
    return bridge


def check_server(server: MCPServerConfig,
                 timeout: float = 10.0) -> dict:
    """Connect + initialize + list_tools for one server; never raises.

    Returns ``{"name", "ok", "protocol_version", "server_info",
    "tool_count", "tools", "error"}``.
    """
    report: dict = {
        "name": server.name,
        "transport": server.transport,
        "ok": False,
        "protocol_version": None,
        "server_info": {},
        "tool_count": 0,
        "tools": [],
        "error": None,
    }
    client = MCPClient(server, request_timeout=timeout)
    try:
        client.connect()
        tools = client.list_tools()
    except Exception as exc:  # noqa: BLE001 — check must never raise
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        try:
            client.close()
        except Exception:
            pass
    report.update(
        ok=True,
        protocol_version=client.protocol_version,
        server_info=client.server_info,
        tool_count=len(tools),
        tools=[t["name"] for t in tools],
    )
    return report
