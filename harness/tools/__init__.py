"""Local tool registry: named tools the agent loop may call."""

from .context import ToolContext
from .plugins import default_plugin_dirs, load_plugins
from .registry import ToolRegistry, list_builtin_tools, safe_path
from .schema import function_schema, normalize_for_ollama

__all__ = [
    "ToolRegistry",
    "ToolContext",
    "list_builtin_tools",
    "safe_path",
    "function_schema",
    "normalize_for_ollama",
    "default_plugin_dirs",
    "load_plugins",
]
