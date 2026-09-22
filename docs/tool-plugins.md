# Writing tool plugins for the harness

Plugins are how anyone adds new tools — no fork required. A plugin is a
single `.py` file in `~/.ttacode/tools/` (user-global) or `./tools/`
(repo-local). The harness imports it at startup and calls
`register_tools(registry, ctx)`.

## Minimal plugin

```python
# ~/.ttacode/tools/my_tools.py
PLUGIN_META = {
    "name": "my-tools",
    "version": "0.1.0",
    "description": "My custom tools.",
}

from harness.tools.schema import function_schema

def _shout(args):
    text = str(args.get("text", ""))
    return {"ok": True, "result": text.upper()}

def register_tools(registry, ctx):
    registry.register(
        "shout",
        "Return text in UPPERCASE.",
        function_schema("shout", "Return text in UPPERCASE.",
                        {"text": "Text to shout"}),
        _shout,
    )
```

Check it: `python -m harness run --headless "use the shout tool on hello"`
lists `shout` among the tools, and the model can call it.

## The contract

* `register_tools(registry, ctx)` — **required**. `registry` is the
  `ToolRegistry`; `ctx` is the `ToolContext` (project root, config,
  checkpoint store).
* `PLUGIN_META` — optional dict with `name`, `version`, `description`.
  Shown in logs; defaults to the filename.
* Handlers take a dict of arguments and return a JSON-serializable dict.
  Return `{"ok": False, "error": "..."}` for failures — **never raise**
  into the model; the loop converts exceptions to error text anyway, but
  clean error dicts give better model behavior.
* Schemas: use `harness.tools.schema.function_schema(name, description,
  properties)` — the same shape as the original `agent_core.schema()`,
  so local models see a familiar calling convention. `properties` maps
  names to a description string (required string arg) or a dict like
  `{"type": "integer", "description": "...", "required": False,
  "default": 8}`.
* File access: **always** go through `ctx.resolve("relative/path")` —
  it enforces project-root containment and refuses credential files and
  `.git`/`.ttacode` metadata, exactly like the built-in tools.
* Mutating files: use `ctx.snapshot(path, old_bytes, new_bytes)` before
  writing so `restore_change` can undo it (see
  `harness/tools/builtin/files.py` for the pattern).
* Shelling out: prefer `subprocess` with explicit timeouts; never
  `shell=True` with model-controlled strings unescaped. If your
  converter is a CLI (like below), pass arguments as a list.

## Safety rules

* A broken plugin never crashes the harness: import errors, a missing
  `register_tools`, or an exception during registration all log a
  warning to stderr and skip that plugin.
* Plugins run with the user's permissions, like everything else here.
  Only install plugins you trust, and keep them in version control.

## Worked example: "CAD to Text" (illustrative)

> This is a **fictional walkthrough** showing the shape of a realistic
> plugin. It assumes a hypothetical `cad2text` command-line converter
> exists on the user's machine. It is not shipped, and T2C itself is
> not needed — the point is how little code a new tool takes.

Goal: give the model a `cad_to_text` tool that converts a CAD file
(`.step`, `.iges`, `.stl`) inside the project into a text summary
(dimensions, part names, bounding box) the model can reason about.

```python
# ~/.ttacode/tools/cad_tools.py
"""Example plugin: CAD file -> text summary via an external converter."""
import shutil
import subprocess

PLUGIN_META = {
    "name": "cad-tools",
    "version": "0.1.0",
    "description": "Convert CAD files to text summaries (example).",
}

from harness.tools.schema import function_schema

_CONVERTER = shutil.which("cad2text")  # hypothetical external tool

def _cad_to_text(args, ctx):
    rel = str(args.get("path", ""))
    try:
        target = ctx.resolve(rel)
    except (ValueError, PermissionError) as exc:
        return {"ok": False, "error": str(exc)}
    if target.suffix.lower() not in {".step", ".stp", ".iges", ".igs", ".stl"}:
        return {"ok": False, "error": f"Not a CAD file: {rel!r}"}
    if _CONVERTER is None:
        return {"ok": False,
                "error": "cad2text converter not found on PATH."}
    try:
        proc = subprocess.run(
            [_CONVERTER, "--summary", str(target)],
            capture_output=True, text=True, timeout=120, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Converter timed out after 120s."}
    except OSError as exc:
        return {"ok": False, "error": f"Converter failed to start: {exc}"}
    if proc.returncode != 0:
        return {"ok": False,
                "error": f"Converter exit {proc.returncode}: {proc.stderr[-2000:]}"}
    return {"ok": True, "path": rel, "summary": proc.stdout[:12000]}
```

Because the handler needs `ctx`, close over it when registering:

```python
def register_tools(registry, ctx):
    registry.register(
        "cad_to_text",
        "Convert a CAD file (.step/.iges/.stl) in the project to a text "
        "summary via the external cad2text converter.",
        function_schema(
            "cad_to_text",
            "Convert a CAD file to a text summary.",
            {"path": "Relative path to the CAD file inside the project"},
        ),
        lambda args: _cad_to_text(args, ctx),
    )
```

What the user experiences:

1. Drop the file into `~/.ttacode/tools/cad_tools.py`.
2. Run anything — the tool appears automatically:
   `python -m harness run --headless "summarize the bracket in models/bracket.step"`.
3. The model sees `cad_to_text` in its tool list with the description
   above, calls it with `{"path": "models/bracket.step"}`, and gets the
   text summary back as a tool result.

That is the whole integration surface. Jarvis-style growth — websearch,
file control, CAD, whatever comes next — is just more plugins.
