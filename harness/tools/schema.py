"""Tool-schema helpers in the original ``agent_core.schema()`` style.

Schemas follow the Ollama / OpenAI function-calling shape::

    {"type": "function",
     "function": {"name": ..., "description": ...,
                  "parameters": {"type": "object",
                                 "properties": {...},
                                 "required": [...]}}}

``properties`` maps an argument name to either a plain description
string (a required string argument, exactly like ``agent_core.schema``)
or a dict spec ``{"type": ..., "description": ..., "required": bool,
"default": ...}`` for the occasional non-string / optional argument.
"""

from __future__ import annotations


def function_schema(name: str, description: str, properties: dict) -> dict:
    """Build an Ollama-style function tool schema.

    Mirrors ``agent_core.schema()`` so tools ported from the original
    keep the exact calling convention the local models were tuned on.
    """
    params: dict[str, dict] = {}
    required: list[str] = []
    for arg, spec in properties.items():
        if isinstance(spec, str):
            params[arg] = {"type": "string", "description": spec}
            required.append(arg)
        elif isinstance(spec, dict):
            entry = {
                "type": spec.get("type", "string"),
                "description": spec.get("description", ""),
            }
            if "default" in spec:
                entry["default"] = spec["default"]
            params[arg] = entry
            if spec.get("required", True):
                required.append(arg)
        else:
            raise ValueError(f"Bad property spec for {arg!r}: {spec!r}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
            },
        },
    }


def normalize_for_ollama(schema: dict) -> dict:
    """Normalize a registry schema to the Ollama ``tools`` payload shape.

    Accepts the ``function_schema()`` shape (returned unchanged) and the
    older plain parameter-object shape ``{"type": "object", "properties":
    ...}`` used by the first scaffold tools (wrapped automatically).
    """
    if not isinstance(schema, dict):
        raise ValueError("Tool schema must be a dict")
    if schema.get("type") == "function" and isinstance(schema.get("function"), dict):
        return schema
    function = schema.get("function") if isinstance(schema.get("function"), dict) else None
    if function is not None:
        return {"type": "function", "function": function}
    # Plain {"type": "object", "properties": {...}} -> wrap it.
    params = dict(schema)
    params.setdefault("type", "object")
    name = params.pop("_tool_name", "tool")
    description = params.pop("_tool_description", "")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    }
