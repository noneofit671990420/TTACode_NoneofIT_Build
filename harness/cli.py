"""``python -m harness`` command line interface.

Subcommands:

* ``init``            first-run setup: scan models, pick a default, write config
* ``models list``     show discovered models (disk + live Ollama)
* ``models scan``     rescan on-disk model stores and print a summary
* ``run --headless``  run a real headless agent task (Ollama + tools)
* ``mcp list``        list configured MCP servers
* ``skills list``     list discovered skills

Config lives at ``~/.ttacode/config.json``
(``%USERPROFILE%\\.ttacode\\config.json`` on Windows).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _home() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    return _home() / ".ttacode"


def config_path() -> Path:
    return config_dir() / "config.json"


def mcp_config_path() -> Path:
    return config_dir() / "mcp.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _format_size(size_bytes) -> str:
    if not isinstance(size_bytes, int):
        return "unknown size"
    gib = size_bytes / (1024**3)
    if gib >= 1:
        return f"{gib:.1f} GiB"
    return f"{size_bytes / (1024**2):.0f} MiB"


def _print_models(models: list[dict]) -> None:
    if not models:
        print("No models found (no disk stores, no live Ollama servers).")
        return
    print(f"{'MODEL':<48} {'SOURCE':<8} {'SIZE':<12}")
    print("-" * 72)
    for model in models:
        print(
            f"{model['name']:<48} {model['source']:<8} "
            f"{_format_size(model.get('size_bytes')):<12}"
        )


def cmd_init(args: argparse.Namespace) -> int:
    """First-run setup. Read-only scan, never downloads anything."""
    from .models.scanner import discover_all, pick_default

    print("Scanning for models already on this PC (disk stores + live Ollama)…")
    models = discover_all()
    _print_models(models)
    print()

    default = pick_default(models)
    config = load_config()
    if default is None:
        print(
            "No downloaded models found. Install Ollama and pull a model, e.g.\n"
            "  ollama pull qwen3.5:4b\n"
            "then re-run `harness init`. Nothing was downloaded by this command."
        )
        # Still persist the (possibly empty) config so init is idempotent.
        config.setdefault("default_model", None)
    else:
        reason = (
            f"source={default['source']}, size={_format_size(default.get('size_bytes'))}"
        )
        print(f"Default model: {default['name']} ({reason})")
        config["default_model"] = default["name"]
    config["model_sources"] = {
        "ollama_ports": [11434, 11435],
        "scanned_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # Agent-loop tunables (backward compatible: never clobber user values).
    # See harness/agent/loop.py for the "no artificial limits" contract.
    config.setdefault("max_steps", 50)
    config.setdefault("tool_output_limit", 12000)
    config.setdefault("history_keep_turns", 20)
    config.setdefault("context_budget_chars", 100000)
    config.setdefault("project_root", None)
    config.setdefault("shell_timeout", 120)
    path = save_config(config)
    print(f"Config written to {path} (re-running `harness init` is safe).")
    if not mcp_config_path().exists():
        mcp_config_path().write_text(
            json.dumps({"servers": []}, indent=2), encoding="utf-8"
        )
        print(f"Empty MCP config created at {mcp_config_path()}.")
    return 0


def cmd_models_list(args: argparse.Namespace) -> int:
    from .models.scanner import discover_all

    _print_models(discover_all())
    return 0


def cmd_models_scan(args: argparse.Namespace) -> int:
    from .models.scanner import discover_disk_models

    models = discover_disk_models()
    total = sum(m.get("size_bytes") or 0 for m in models)
    print(f"Disk scan: {len(models)} model(s) in on-disk stores.")
    _print_models(models)
    print(f"Total on-disk (known sizes): {_format_size(total)}")
    return 0


def _resolve_model_url(model: str, ports: tuple[int, ...] = (11434, 11435)) -> str | None:
    """Pick a reachable Ollama endpoint that serves ``model``.

    Prefers a server whose /api/tags actually lists the model (or its
    :latest variant); falls back to the first reachable server; None
    when nothing answers.
    """
    from .models.scanner import discover_live_models

    live = discover_live_models(ports)
    for port in ports:
        names = live.get(port, [])
        if model in names or model + ":latest" in names or model.split(":")[0] in names:
            return f"http://127.0.0.1:{port}"
    for port in ports:
        if _is_reachable(f"http://127.0.0.1:{port}"):
            return f"http://127.0.0.1:{port}"
    return None


def _is_reachable(url: str) -> bool:
    from .transports.ollama import is_reachable

    return is_reachable(url)


def cmd_run(args: argparse.Namespace) -> int:
    from .agent.loop import AgentLoop
    from .models.scanner import discover_all, pick_default
    from .skills.loader import discover_skills
    from .tools import ToolRegistry, ToolContext, load_plugins
    from .tools.builtin import register_all
    from .transports.ollama import OllamaTransport

    if not args.headless:
        print("Only --headless mode is supported by the harness CLI.", file=sys.stderr)
        return 2

    config = load_config()
    project = args.project or config.get("project_root") or os.getcwd()
    project_path = Path(project).expanduser()
    if not project_path.is_dir():
        print(f"Error: project directory does not exist: {project}", file=sys.stderr)
        return 1

    # Model: explicit flag > config default > auto-pick from disk scan.
    model = args.model or config.get("default_model")
    if not model:
        default = pick_default(discover_all())
        if default is None:
            print(
                "No downloaded models found. Run `harness init` for guidance.",
                file=sys.stderr,
            )
            return 1
        model = default["name"]
    url = _resolve_model_url(model)
    if url is None:
        print(
            f"Error: no reachable Ollama server on :11434/:11435 serves {model!r}.\n"
            "Start Ollama (`ollama serve`) and make sure the model is pulled, "
            "then re-run. `harness models list` shows what this PC has.",
            file=sys.stderr,
        )
        return 1

    # Loop tunables: CLI flag > config > built-in defaults (see AgentLoop).
    if args.max_steps is not None:
        config["max_steps"] = args.max_steps

    ctx = ToolContext(project_root=project_path, config=config)
    registry = ToolRegistry()
    builtin_added = register_all(registry, ctx)
    plugins = load_plugins(registry, ctx)
    for failure in plugins["failed"]:
        print(f"Warning: plugin skipped ({failure['path']}): {failure['error']}",
              file=sys.stderr)

    skills = discover_skills()
    transport = OllamaTransport(url, model)
    loop = AgentLoop(
        transport=transport,
        model=model,
        tools=registry,
        skills=skills,
        config=config,
        project_root=project_path,
        tool_context=ctx,
    )
    print(f"Model : {model} ({url})")
    print(f"Project: {project_path}")
    print(f"Tools : {len(builtin_added)} built-in"
          + (f" + {len(plugins['loaded'])} plugin(s)" if plugins["loaded"] else "")
          + (f", {len(skills)} skill(s)" if skills else ""))
    print("-" * 60)
    result = loop.run(args.prompt)
    print()
    print(result["result"])
    print("-" * 60)
    print(f"Steps: {result['steps']}  "
          f"Tool calls: {len(result['tool_calls'])}  "
          f"Stopped: {result['stopped_reason']}")
    if result["stopped_reason"] == "transport_error":
        return 1
    return 0


def cmd_mcp_list(args: argparse.Namespace) -> int:
    from .mcp.client import load_servers

    try:
        servers = load_servers(mcp_config_path())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not servers:
        print(f"No MCP servers configured ({mcp_config_path()}).")
        return 0
    for server in servers:
        via = f"command: {' '.join(server.command)}" if server.transport == "stdio" else f"url: {server.url}"
        print(f"- {server.name} [{server.transport}] {via}")
    print("\nNote: MCP transport is not yet implemented (docs/mcp-roadmap.md).")
    return 0


def cmd_skills_list(args: argparse.Namespace) -> int:
    from .skills.loader import discover_skills

    skills = discover_skills()
    if not skills:
        print("No skills discovered (~/.ttacode/skills, ./skills).")
        return 0
    for skill in skills:
        version = f" v{skill['version']}" if skill.get("version") else ""
        print(f"- {skill['name']}{version}: {skill.get('description', '')}")
        print(f"    {skill['path']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="TTACode modular headless harness (stdlib-only core).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="First-run setup: scan models, pick default, write config.")
    p_init.set_defaults(func=cmd_init)

    p_models = sub.add_parser("models", help="Model discovery commands.")
    msub = p_models.add_subparsers(dest="models_command", required=True)
    p_list = msub.add_parser("list", help="List discovered models (disk + live).")
    p_list.set_defaults(func=cmd_models_list)
    p_scan = msub.add_parser("scan", help="Rescan on-disk model stores.")
    p_scan.set_defaults(func=cmd_models_scan)

    p_run = sub.add_parser("run", help="Run a headless agent task.")
    p_run.add_argument("--headless", action="store_true", help="Headless mode (required).")
    p_run.add_argument("--project", default=None,
                       help="Project directory (default: config project_root or cwd).")
    p_run.add_argument("--model", default=None,
                       help="Model name (default: config default_model or auto-pick).")
    p_run.add_argument("--max-steps", type=int, default=None,
                       help="Max agent steps (default: config max_steps, 50).")
    p_run.add_argument("prompt", help="The task prompt.")
    p_run.set_defaults(func=cmd_run)

    p_mcp = sub.add_parser("mcp", help="MCP server commands.")
    mcsub = p_mcp.add_subparsers(dest="mcp_command", required=True)
    p_mcp_list = mcsub.add_parser("list", help="List configured MCP servers.")
    p_mcp_list.set_defaults(func=cmd_mcp_list)

    p_skills = sub.add_parser("skills", help="Skill commands.")
    ssub = p_skills.add_subparsers(dest="skills_command", required=True)
    p_skills_list = ssub.add_parser("list", help="List discovered skills.")
    p_skills_list.set_defaults(func=cmd_skills_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
