"""Built-in tool groups for the harness.

Each module exposes ``register_tools(registry, ctx)``. ``register_all``
wires every group into one registry — this is also the pattern
third-party plugins follow (see ``harness/tools/plugins.py`` and
``docs/tool-plugins.md``).
"""

from __future__ import annotations

from ..context import ToolContext


def register_all(registry, ctx: ToolContext) -> list[str]:
    """Register every built-in tool group. Returns the tool names added."""
    from . import files, shell, web

    before = {t["name"] for t in registry.list_tools()}
    for module in (files, shell, web):
        module.register_tools(registry, ctx)
    after = [t["name"] for t in registry.list_tools()]
    return [name for name in after if name not in before]
