"""Tool registry: named tools the agent loop may call.

Tools are registered as ``(name, description, schema, handler)`` where the
handler receives a dict of arguments and returns a JSON-serializable
result. Built-in tool groups live in :mod:`harness.tools.builtin`
(files, shell, web); third-party plugins in :mod:`harness.tools.plugins`.
Path containment is enforced by :class:`harness.tools.context.ToolContext`
(the same idea as the original ``app.py::safe_path``).
"""

from __future__ import annotations

from pathlib import Path


def safe_path(project: str | Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``project``; raise if it escapes the root."""
    base = Path(project).expanduser().resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"Path escapes the project root: {rel!r}")
    return target


class ToolRegistry:
    """Name -> (description, schema, handler) registry."""

    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}

    def register(self, name: str, description: str, schema: dict, handler) -> None:
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid tool name: {name!r}")
        self._tools[name] = {
            "name": name,
            "description": description,
            "schema": schema,
            "handler": handler,
        }

    def get(self, name: str) -> dict | None:
        """Return the tool record, or None when unknown."""
        return self._tools.get(name)

    def unregister(self, name: str) -> bool:
        """Remove a tool; returns True when one was registered."""
        return self._tools.pop(name, None) is not None

    def call(self, name: str, arguments: dict):
        """Invoke a tool handler. Raises KeyError for unknown tools."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a dict")
        return tool["handler"](arguments)

    def list_tools(self) -> list[dict]:
        """Name + description for every registered tool (no handlers)."""
        return [
            {"name": name, "description": tool["description"]}
            for name, tool in sorted(self._tools.items())
        ]


def list_builtin_tools(project_root: str | Path) -> ToolRegistry:
    """Registry pre-loaded with all built-in tool groups.

    Delegates to :mod:`harness.tools.builtin` (files, shell, web). The
    two original proof-of-concept tools (``read_file`` / ``list_dir``)
    are now the full ported versions with identical names.
    """
    from .builtin import register_all
    from .context import ToolContext

    root = Path(project_root).expanduser()
    ctx = ToolContext(project_root=root)
    registry = ToolRegistry()
    register_all(registry, ctx)
    return registry
