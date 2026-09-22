"""``python -m harness`` command line interface.

Subcommands:

* ``init``            first-run setup: scan models, pick a default, write config
* ``models list``     show discovered models (disk + live Ollama)
* ``models scan``     rescan on-disk model stores and print a summary
* ``models warm``     pre-load a model into VRAM so the first run is fast
* ``run --headless``  run a real headless agent task (Ollama + tools + MCP)
* ``mcp list``        list configured MCP servers (live status + tools)
* ``mcp check``       test each MCP server (initialize + tools/list)
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


def _print_models(models: list[dict], vram_gb: float | None = None) -> None:
    if not models:
        print("No models found (no disk stores, no live Ollama servers).")
        return
    budget = (
        int(vram_gb * 0.9 * (1024**3))
        if isinstance(vram_gb, (int, float)) and vram_gb > 0
        else None
    )
    fit_col = budget is not None
    header = f"{'MODEL':<48} {'SOURCE':<8} {'SIZE':<12}"
    if fit_col:
        header += " FIT"
    print(header)
    print("-" * (72 + (4 if fit_col else 0)))
    for model in models:
        size = model.get("size_bytes")
        marker = ""
        if fit_col:
            if isinstance(size, int):
                marker = "✓" if size <= budget else "!"
            else:
                marker = "?"
        line = (
            f"{model['name']:<48} {model['source']:<8} "
            f"{_format_size(size):<12}"
        )
        if fit_col:
            line += f" {marker}"
        print(line)
    if fit_col:
        print("FIT: ✓ fits VRAM budget (90%) · ! exceeds budget, expect CPU spill · "
              "? size unknown")
    lm_only = [m for m in models
               if m.get("source") == "disk" and m.get("store") == "lmstudio"]
    if lm_only:
        print("Note: LM Studio 'disk' files are not served by Ollama — import one with "
              "`ollama create <name> -f Modelfile` (with a `FROM <path-to-gguf>` line) "
              "to make it usable.")


def cmd_init(args: argparse.Namespace) -> int:
    """First-run setup. Read-only scan, never downloads anything."""
    from .models.loader import detect_vram_gb
    from .models.scanner import discover_all, pick_default

    print("Scanning for models already on this PC (disk stores + live Ollama)…")
    models = discover_all()

    config = load_config()
    # VRAM detection (best-effort; never clobbers an existing value).
    if "vram_gb" not in config:
        detected = detect_vram_gb()
        if detected:
            config["vram_gb"] = detected
            print(f"Detected GPU VRAM: {detected:.1f} GiB (via nvidia-smi).")
        else:
            config["vram_gb"] = 8.0
            print("Could not detect GPU VRAM (no nvidia-smi); assuming 8.0 GiB — "
                  "adjust `vram_gb` in the config if that's wrong.")
    vram_gb = config.get("vram_gb")
    _print_models(models, vram_gb=vram_gb)
    print()

    default = pick_default(models, vram_gb=vram_gb)
    if default is None:
        lm_count = sum(
            1 for m in models
            if m.get("source") == "disk" and m.get("store") == "lmstudio"
        )
        if lm_count:
            print(
                f"Found {lm_count} LM Studio file(s), but no model Ollama can serve. "
                "Import one into Ollama, e.g.\n"
                "  ollama create mymodel -f Modelfile   # Modelfile: FROM <path-to-gguf>\n"
                "or pull a ready model with `ollama pull qwen2.5-coder:7b`,\n"
                "then re-run `harness init`. Nothing was downloaded by this command."
            )
        else:
            print(
                "No downloaded models found. Install Ollama and pull a model, e.g.\n"
                "  ollama pull qwen2.5-coder:7b\n"
                "then re-run `harness init`. Nothing was downloaded by this command."
            )
        # Still persist the (possibly empty) config so init is idempotent.
        config.setdefault("default_model", None)
    else:
        reason = (
            f"source={default['source']}, size={_format_size(default.get('size_bytes'))}"
        )
        size = default.get("size_bytes")
        budget = (
            int(vram_gb * 0.9 * (1024**3))
            if isinstance(vram_gb, (int, float)) and vram_gb > 0 and isinstance(size, int)
            else None
        )
        if budget is not None and size > budget:
            reason += " — WARNING: exceeds 90% VRAM budget, expect CPU spill"
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
    # Local-model performance tunables (see docs/performance.md).
    # keep_alive keeps the model warm in VRAM between runs — the biggest
    # latency win on consumer GPUs. ollama_num_gpu=-1 asks Ollama to
    # offload as many layers as the GPU can hold (its own default);
    # 0 would disable GPU offload entirely.
    config.setdefault("ollama_keep_alive", "30m")
    config.setdefault("ollama_num_ctx", 8192)
    config.setdefault("ollama_num_gpu", -1)
    # Model-load behavior (see harness/models/loader.py). warm_on_load
    # makes every `run` pre-load the model into VRAM automatically.
    config.setdefault("warm_on_load", True)
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

    _print_models(discover_all(), vram_gb=load_config().get("vram_gb"))
    return 0


def cmd_models_scan(args: argparse.Namespace) -> int:
    from .models.scanner import discover_disk_models

    models = discover_disk_models()
    total = sum(m.get("size_bytes") or 0 for m in models)
    print(f"Disk scan: {len(models)} model(s) in on-disk stores.")
    _print_models(models)
    print(f"Total on-disk (known sizes): {_format_size(total)}")
    return 0


def cmd_models_warm(args: argparse.Namespace) -> int:
    """Pre-load a model into VRAM so the first real run is fast.

    Manual version of what `run --headless` now does automatically at
    load time (see harness/models/loader.py).
    """
    from .models.loader import ModelLoadError, load_model

    config = load_config()
    try:
        loaded = load_model(args.model, config, warm=True)
    except ModelLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if loaded.warmed:
        print(f"{loaded.name} is warm and ready.")
        return 0
    print(f"Error: warm-up request failed for {loaded.name!r}.", file=sys.stderr)
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    from .agent.loop import AgentLoop
    from .mcp.bridge import mount_mcp_tools
    from .models.loader import ModelLoadError, load_model
    from .skills.loader import discover_skills
    from .tools import ToolRegistry, ToolContext, load_plugins
    from .tools.builtin import register_all

    if not args.headless:
        print("Only --headless mode is supported by the harness CLI.", file=sys.stderr)
        return 2

    config = load_config()
    project = args.project or config.get("project_root") or os.getcwd()
    project_path = Path(project).expanduser()
    if not project_path.is_dir():
        print(f"Error: project directory does not exist: {project}", file=sys.stderr)
        return 1

    # Model load: resolve → tune (num_gpu/num_ctx/keep_alive) → warm into
    # VRAM. One path for the whole CLI (see harness/models/loader.py).
    try:
        loaded = load_model(args.model, config, warm=not args.no_warm)
    except ModelLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    model = loaded.name
    url = loaded.url
    transport = loaded.transport

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
    # MCP servers: dead ones are warned about and skipped — never fatal.
    mcp_bridge = mount_mcp_tools(registry, ctx)
    for failure in mcp_bridge.failed:
        print(f"Warning: MCP server {failure['name']!r} skipped: {failure['error']}",
              file=sys.stderr)

    skills = discover_skills()
    loop = AgentLoop(
        transport=transport,
        model=model,
        tools=registry,
        skills=skills,
        config=config,
        project_root=project_path,
        tool_context=ctx,
        mcp_bridge=mcp_bridge,
    )
    print(f"Model : {model} ({url})")
    print(f"Project: {project_path}")
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
    print(f"Tools : {' + '.join(tool_bits)}")
    print("-" * 60)
    try:
        result = loop.run(args.prompt)
    finally:
        loop.close()
    print()
    print(result["result"])
    print("-" * 60)
    print(f"Steps: {result['steps']}  "
          f"Tool calls: {len(result['tool_calls'])}  "
          f"Stopped: {result['stopped_reason']}")
    if result["stopped_reason"] == "transport_error":
        return 1
    return 0


def _describe_server(server) -> str:
    if server.transport == "stdio":
        return f"command: {' '.join([*server.command, *server.args])}"
    return f"url: {server.url}"


def cmd_mcp_list(args: argparse.Namespace) -> int:
    from .mcp.bridge import check_server
    from .mcp.client import load_servers

    try:
        servers = load_servers(mcp_config_path())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not servers:
        print(f"No MCP servers configured ({mcp_config_path()}).")
        print("Add entries to enable MCP tools; see docs/mcp.md.")
        return 0
    for server in servers:
        report = check_server(server, timeout=8.0)
        if report["ok"]:
            tools = ", ".join(report["tools"][:8])
            more = f" (+{report['tool_count'] - 8} more)" if report["tool_count"] > 8 else ""
            proto = f"proto {report['protocol_version']}" if report["protocol_version"] else ""
            print(f"- {server.name} [{server.transport}] {_describe_server(server)}")
            print(f"    OK {proto}: {report['tool_count']} tool(s): {tools}{more}")
        else:
            print(f"- {server.name} [{server.transport}] {_describe_server(server)}")
            print(f"    unreachable: {report['error']}")
    return 0


def cmd_mcp_check(args: argparse.Namespace) -> int:
    from .mcp.bridge import check_server
    from .mcp.client import load_servers

    try:
        servers = load_servers(mcp_config_path())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not servers:
        print(f"No MCP servers configured ({mcp_config_path()}).")
        return 1
    failed = 0
    for server in servers:
        report = check_server(server, timeout=10.0)
        if report["ok"]:
            info = report["server_info"] or {}
            label = info.get("name") or server.name
            print(f"OK   {server.name}: {label} "
                  f"(proto {report['protocol_version']}, "
                  f"{report['tool_count']} tools)")
        else:
            failed += 1
            print(f"FAIL {server.name}: {report['error']}")
    print(f"{len(servers) - failed}/{len(servers)} server(s) OK.")
    return 1 if failed else 0


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
    # The PyInstaller binary is named ttacode(.exe); python -m stays "harness".
    prog = "ttacode" if Path(sys.argv[0]).stem == "ttacode" else "harness"
    parser = argparse.ArgumentParser(
        prog=prog,
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
    p_warm = msub.add_parser("warm", help="Pre-load a model into VRAM so the first run is fast.")
    p_warm.add_argument("model", nargs="?", default=None,
                        help="Model name (default: config default_model or auto-pick).")
    p_warm.set_defaults(func=cmd_models_warm)

    p_run = sub.add_parser("run", help="Run a headless agent task.")
    p_run.add_argument("--headless", action="store_true", help="Headless mode (required).")
    p_run.add_argument("--project", default=None,
                       help="Project directory (default: config project_root or cwd).")
    p_run.add_argument("--model", default=None,
                       help="Model name (default: config default_model or auto-pick).")
    p_run.add_argument("--max-steps", type=int, default=None,
                       help="Max agent steps (default: config max_steps, 50).")
    p_run.add_argument("--no-warm", action="store_true",
                       help="Skip the automatic VRAM warm-up at model load.")
    p_run.add_argument("prompt", help="The task prompt.")
    p_run.set_defaults(func=cmd_run)

    p_mcp = sub.add_parser("mcp", help="MCP server commands.")
    mcsub = p_mcp.add_subparsers(dest="mcp_command", required=True)
    p_mcp_list = mcsub.add_parser("list", help="List configured MCP servers (live status).")
    p_mcp_list.set_defaults(func=cmd_mcp_list)
    p_mcp_check = mcsub.add_parser("check", help="Test each MCP server (initialize + tools/list).")
    p_mcp_check.set_defaults(func=cmd_mcp_check)

    p_skills = sub.add_parser("skills", help="Skill commands.")
    ssub = p_skills.add_subparsers(dest="skills_command", required=True)
    p_skills_list = ssub.add_parser("list", help="List discovered skills.")
    p_skills_list.set_defaults(func=cmd_skills_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
