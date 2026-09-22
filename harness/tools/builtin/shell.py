"""Shell tool: run commands with the project root as working directory.

This is what lets the harness "code and push": ``git``, ``pytest``,
compilers and build scripts all run through ``run_command``. Portable
across Windows and POSIX:

* Windows: ``powershell.exe -NoProfile -NonInteractive -Command <cmd>``
  (same as the original ``agent_core``).
* Other systems: the platform shell via ``/bin/sh -c <cmd>``.

Timeouts are enforced with ``Popen.communicate(timeout=...)`` plus an
explicit kill — no external taskkill/killpg needed. Output is the tail
of merged stdout+stderr (like the original's 20 KB tail).
"""

from __future__ import annotations

import os
import subprocess

from ..context import ToolContext
from ..schema import function_schema

_TAIL_CHARS = 20_000  # mirrors agent_core's 20000-byte tail


def register_tools(registry, ctx: ToolContext) -> None:
    default_timeout = int(ctx.config.get("shell_timeout", 120))

    def run_command(args: dict) -> dict:
        command = str(args.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "command must not be empty."}
        try:
            timeout = int(args.get("timeout", default_timeout))
        except (TypeError, ValueError):
            timeout = default_timeout
        timeout = max(5, min(timeout, 1800))
        cwd_rel = str(args.get("cwd", ".") or ".")
        try:
            cwd = ctx.resolve(cwd_rel)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not cwd.is_dir():
            return {"ok": False, "error": f"Not a directory: {cwd_rel!r}"}

        if os.name == "nt":
            argv = ["powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Command", command]
            extra: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            argv = ["/bin/sh", "-c", command]
            extra = {}
        try:
            # The context manager guarantees the process is reaped (wait),
            # so no ResourceWarnings and no zombie processes.
            with subprocess.Popen(
                argv, cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                errors="replace", **extra,
            ) as process:
                try:
                    output, _ = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                    return {"ok": False,
                            "error": f"Command timed out after {timeout}s and was killed.",
                            "returncode": None,
                            "output": (output or "")[-_TAIL_CHARS:]}
                output = (output or "")[-_TAIL_CHARS:]
                return {"ok": True, "returncode": process.returncode,
                        "output": f"Exit {process.returncode}\n{output}"}
        except OSError as exc:
            return {"ok": False, "error": f"Could not start command: {exc}"}

    registry.register(
        "run_command",
        "Run a shell command with the project as working directory "
        "(PowerShell on Windows, sh elsewhere). Use for builds, tests, git, "
        "and any project tooling. Merged stdout+stderr tail is returned.",
        function_schema(
            "run_command",
            "Run a shell command with the project as working directory.",
            {
                "command": "Shell command to run",
                "timeout": {"type": "integer",
                            "description": "Timeout in seconds (5-1800)",
                            "required": False, "default": default_timeout},
                "cwd": {"type": "string",
                        "description": "Working directory relative to project root",
                        "required": False, "default": "."},
            },
        ),
        run_command,
    )
