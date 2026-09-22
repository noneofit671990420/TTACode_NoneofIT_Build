"""Third-party tool plugins: the "add your own tool" story.

A plugin is a single ``.py`` file living in one of the plugin dirs
(defaults: ``~/.ttacode/tools/*.py`` and ``./tools/*.py``). It must
expose::

    PLUGIN_META = {"name": "my-tools", "version": "0.1.0",
                   "description": "What this plugin adds."}   # optional

    def register_tools(registry, ctx):
        registry.register("my_tool", "What it does.",
                          function_schema("my_tool", "...", {...}),
                          my_handler)

Plugins receive the same ``ToolContext`` as built-ins (project root,
config, checkpoint store) and should import
``harness.tools.schema.function_schema`` for their schemas — see
``docs/tool-plugins.md`` for a full worked example ("CAD to Text").

Loading is defensive by design: a broken plugin prints a warning to
stderr and is skipped. One bad plugin never takes down the harness.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path


def _home() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    return Path(os.path.expanduser("~"))


def default_plugin_dirs() -> list[Path]:
    """Where plugins are discovered (user-global, then repo-local)."""
    return [_home() / ".ttacode" / "tools", Path.cwd() / "tools"]


def _load_module(path: Path):
    """Import one plugin file. Raises on any failure (caller handles)."""
    module_name = "ttacode_plugin_" + "".join(
        c if c.isalnum() else "_" for c in path.stem
    )
    # Re-imports pick up edits: drop a stale cached module first.
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_plugins(registry, ctx, dirs: list[str | Path] | None = None) -> dict:
    """Discover and register plugins.

    Returns ``{"loaded": [meta, ...], "failed": [{"path":..., "error":...}]}``.
    Never raises: every failure mode is captured per-plugin.
    """
    search = [Path(d) for d in dirs] if dirs is not None else default_plugin_dirs()
    loaded: list[dict] = []
    failed: list[dict] = []
    for base in search:
        try:
            candidates = sorted(base.glob("*.py"))
        except OSError:
            continue
        for path in candidates:
            if path.name.startswith("__"):
                continue
            try:
                module = _load_module(path)
            except Exception:
                failed.append({"path": str(path),
                               "error": traceback.format_exc(limit=3).strip().splitlines()[-1]})
                print(f"[harness] plugin failed to import, skipping: {path}",
                      file=sys.stderr)
                continue
            register = getattr(module, "register_tools", None)
            if not callable(register):
                failed.append({"path": str(path),
                               "error": "missing register_tools(registry, ctx)"})
                print(f"[harness] plugin has no register_tools(), skipping: {path}",
                      file=sys.stderr)
                continue
            try:
                before = {t["name"] for t in registry.list_tools()}
                register(registry, ctx)
                added = sorted({t["name"] for t in registry.list_tools()} - before)
            except Exception:
                # Roll back anything the plugin registered before raising,
                # so a half-registered plugin leaves no stray tools behind.
                for name in sorted({t["name"] for t in registry.list_tools()} - before):
                    registry.unregister(name)
                failed.append({"path": str(path),
                               "error": traceback.format_exc(limit=3).strip().splitlines()[-1]})
                print(f"[harness] plugin register_tools() raised, skipping: {path}",
                      file=sys.stderr)
                continue
            meta = getattr(module, "PLUGIN_META", None)
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta.setdefault("name", path.stem)
            meta.setdefault("path", str(path))
            meta["tools"] = added
            loaded.append(meta)
    loaded.sort(key=lambda m: m.get("name", ""))
    return {"loaded": loaded, "failed": failed}
