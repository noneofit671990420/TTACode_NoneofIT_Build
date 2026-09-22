"""``python -m harness`` command line interface.

Subcommands:

* ``init``            first-run setup: scan models, pick a default, write config
* ``models list``     show discovered models (disk + live Ollama)
* ``models scan``     rescan on-disk model stores and print a summary
* ``run --headless``  resolve a model and print the plan (agent loop: roadmap)
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


def cmd_run(args: argparse.Namespace) -> int:
    from .models.scanner import discover_all, pick_default
    from .transports.ollama import is_reachable

    if not args.headless:
        print("Only --headless mode is supported by the harness CLI.", file=sys.stderr)
        return 2

    models = discover_all()
    default = pick_default(models)
    if default is None:
        print(
            "No downloaded models found. Run `harness init` for guidance.",
            file=sys.stderr,
        )
        return 1

    # Prefer a live server that actually has the model; else disk-only note.
    url = "http://127.0.0.1:11434"
    route = "local (Ollama :11434)"
    reason = f"downloaded on disk ({default['source']})"
    if not is_reachable(url):
        route = "disk-only (no live Ollama on :11434)"
        reason = "model is downloaded but no Ollama server is running"

    print("Resolved run plan:")
    print(f"  route : {route}")
    print(f"  model : {default['name']}")
    print(f"  why   : {reason}")
    print(f"  prompt: {args.prompt[:120]}")
    print()
    print(
        "NOT-YET-IMPLEMENTED: the headless agent loop is scaffolded but not "
        "wired up (see docs/harness-roadmap.md). No model was contacted and "
        "no tools were executed."
    )
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

    p_run = sub.add_parser("run", help="Run a headless agent task (skeleton).")
    p_run.add_argument("--headless", action="store_true", help="Headless mode (required).")
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
