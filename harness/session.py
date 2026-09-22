"""One shared agent-session build path for the CLI and the desktop GUI.

``build_session()`` does everything both frontends need: config,
project dir, model resolve → tune → warm, tools, plugins, MCP, skills,
and the :class:`~harness.agent.loop.AgentLoop`. It returns a
:class:`Session` and raises instead of printing, so the CLI can format
errors for a terminal and the GUI can show them in a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent.loop import AgentLoop
from .mcp.bridge import mount_mcp_tools
from .models.loader import ModelLoadError, load_model
from .skills.loader import discover_skills
from .tools import ToolRegistry, ToolContext, load_plugins
from .tools.builtin import register_all


class SessionError(Exception):
    """Session setup failed in a way the frontend should report."""


@dataclass
class Session:
    loop: AgentLoop
    loaded: object  # harness.models.loader.LoadedModel
    project_path: Path
    warnings: list[str] = field(default_factory=list)
    tool_summary: str = ""


def default_project_dir(config: dict) -> Path:
    """Project dir for a session: config ``project_root`` or the cwd."""
    project = config.get("project_root")
    if project:
        return Path(project).expanduser()
    return Path.cwd()


def build_session(
    *,
    model: str | None = None,
    project: str | Path | None = None,
    max_steps: int | None = None,
    warm: bool = True,
    config: dict | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Session:
    """Build a ready-to-chat agent session.

    * ``model`` — explicit model name, or ``None`` to auto-pick the
      VRAM-aware default from what's installed.
    * ``project`` — working directory for file/shell tools, or ``None``
      for :func:`default_project_dir`.
    * ``max_steps`` — per-turn step cap override.
    * ``warm`` — pre-load the model into VRAM (first answer is fast).
    * ``config`` — config dict (mutated with ``max_steps`` when given,
      same as the CLI).
    * ``on_progress`` — called with short status lines
      ("Warming qwen2.5-coder:7b into VRAM…").

    Raises :class:`SessionError` (bad project dir) or
    :class:`~harness.models.loader.ModelLoadError` (honest model errors
    with ``ollama serve`` / ``ollama pull`` guidance).
    """
    from .cli import load_config  # deferred: cli owns config file layout

    config = dict(config if config is not None else load_config())
    say = on_progress if on_progress is not None else (lambda _msg: None)

    project_path = Path(project).expanduser() if project else default_project_dir(config)
    if not project_path.is_dir():
        raise SessionError(f"Project directory does not exist: {project_path}")

    # Model load: resolve → tune (num_gpu/num_ctx/keep_alive) → warm into
    # VRAM. One path for the whole product (see harness/models/loader.py).
    say("Resolving model…")
    loaded = load_model(model, config, warm=warm, out=say)

    # Loop tunables: explicit arg > config > built-in defaults.
    if max_steps is not None:
        config["max_steps"] = max_steps

    say("Loading tools…")
    ctx = ToolContext(project_root=project_path, config=config)
    registry = ToolRegistry()
    builtin_added = register_all(registry, ctx)
    plugins = load_plugins(registry, ctx)
    warnings = [
        f"Plugin skipped ({failure['path']}): {failure['error']}"
        for failure in plugins["failed"]
    ]
    # MCP servers: dead ones are warned about and skipped — never fatal.
    mcp_bridge = mount_mcp_tools(registry, ctx)
    warnings.extend(
        f"MCP server {failure['name']!r} skipped: {failure['error']}"
        for failure in mcp_bridge.failed
    )

    skills = discover_skills()
    loop = AgentLoop(
        transport=loaded.transport,
        model=loaded.name,
        tools=registry,
        skills=skills,
        config=config,
        project_root=project_path,
        tool_context=ctx,
        mcp_bridge=mcp_bridge,
    )
    tool_bits = [f"{len(builtin_added)} built-in"]
    if plugins["loaded"]:
        tool_bits.append(f"{len(plugins['loaded'])} plugin(s)")
    if mcp_bridge.tool_count:
        tool_bits.append(
            f"{mcp_bridge.tool_count} MCP tool(s) "
            f"from {mcp_bridge.server_count} server(s)"
        )
    if skills:
        tool_bits.append(f"{len(skills)} skill(s)")
    return Session(
        loop=loop,
        loaded=loaded,
        project_path=project_path,
        warnings=warnings,
        tool_summary=" + ".join(tool_bits),
    )


def close_session(session: Session) -> None:
    """Release session resources (MCP bridge)."""
    try:
        session.loop.close()
    except Exception:
        pass
