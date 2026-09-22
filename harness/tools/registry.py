"""Minimal tool registry with 2-3 built-in proof-of-concept tools.

Tools are registered as ``(name, description, schema, handler)`` where the
handler receives a dict of arguments and returns a JSON-serializable
result. The built-ins are bounded to a project root with the same
path-escape protection as the original ``app.py::safe_path``.
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


def _make_read_file(project_root: Path):
    def read_file(arguments: dict) -> dict:
        rel = str(arguments.get("path", ""))
        target = safe_path(project_root, rel)
        if not target.is_file():
            return {"ok": False, "error": f"Not a file: {rel!r}"}
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        if len(text) > 200_000:
            text = text[:200_000] + "\n…[truncated]"
        return {"ok": True, "path": rel, "text": text}

    return read_file


def _make_list_dir(project_root: Path):
    def list_dir(arguments: dict) -> dict:
        rel = str(arguments.get("path", "."))
        target = safe_path(project_root, rel)
        if not target.is_dir():
            return {"ok": False, "error": f"Not a directory: {rel!r}"}
        try:
            entries = sorted(
                p.name + ("/" if p.is_dir() else "")
                for p in target.iterdir()
                if not p.name.startswith(".")
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": rel, "entries": entries[:500]}

    return list_dir


def list_builtin_tools(project_root: str | Path) -> ToolRegistry:
    """Registry pre-loaded with the bounded proof-of-concept tools."""
    root = Path(project_root).expanduser()
    registry = ToolRegistry()
    registry.register(
        "read_file",
        "Read a UTF-8 text file inside the project root (200KB cap).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        _make_read_file(root),
    )
    registry.register(
        "list_dir",
        "List entries of a directory inside the project root (dotfiles hidden).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        _make_list_dir(root),
    )
    return registry
