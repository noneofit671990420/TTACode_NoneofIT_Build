"""Built-in file tools, ported from ``agent_core.ProjectTools``.

Every mutating tool snapshots the previous bytes through
``ToolContext.snapshot`` before writing, so ``restore_change`` can undo
it — the same checkpoint discipline as the original
``agent_core._write_file`` / ``restore_checkpoint``.

All handlers return plain dicts (``{"ok": True, ...}`` /
``{"ok": False, "error": ...}``); errors are data for the model, never
exceptions escaping into the loop. Path containment comes from
``ToolContext.resolve``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re

from ..context import ToolContext
from ..schema import function_schema

# Directories never walked by grep_search / list helpers.
_SKIP_DIRS = {
    ".git", ".godot", "node_modules", "__pycache__", ".venv", "venv",
    ".ttacode", ".talktoai-code", "Library", "Temp", "obj", "bin",
    "vendor", "artifacts", "build", "dist",
}

_READ_LIMIT = 250_000      # bytes, mirrors agent_core read_file
_WRITE_LIMIT = 500_000     # bytes, mirrors agent_core _write_file
_DIFF_PREVIEW = 18_000     # chars of unified diff echoed back
_GREP_FILE_LIMIT = 1_000_000


def _read_text_capped(path, limit=_READ_LIMIT) -> str:
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"File exceeds the {limit // 1000} KB text limit.")
    return path.read_text(encoding="utf-8", errors="replace")


def register_tools(registry, ctx: ToolContext) -> None:
    def read_file(args: dict) -> dict:
        try:
            target = ctx.resolve(args["path"])
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not target.is_file():
            return {"ok": False, "error": f"Not a file: {args['path']!r}"}
        try:
            return {"ok": True, "path": args["path"], "text": _read_text_capped(target)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def file_fingerprint(args: dict) -> dict:
        try:
            target = ctx.resolve(args["path"])
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not target.exists():
            return {"ok": True, "path": args["path"], "exists": False,
                    "sha256": None, "bytes": 0}
        try:
            data = target.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": args["path"], "exists": True,
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def _do_write(path_rel: str, content: str) -> dict:
        if len(content.encode("utf-8")) > _WRITE_LIMIT:
            return {"ok": False,
                    "error": f"Generated file exceeds {_WRITE_LIMIT // 1000} KB."}
        try:
            target = ctx.resolve(path_rel)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        try:
            old_bytes = target.read_bytes() if target.exists() else None
            before = old_bytes.decode("utf-8", errors="replace") if old_bytes else ""
            new_bytes = content.encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Snapshot BEFORE mutating: the checkpoint records the
            # original bytes and the expected new hash. If the write
            # below fails, the checkpoint is discarded so history never
            # claims a change that didn't happen.
            checkpoint_id = ctx.snapshot(target, old_bytes, new_bytes)
            try:
                target.write_bytes(new_bytes)
            except OSError as exc:
                ctx.discard_checkpoint(checkpoint_id)
                return {"ok": False, "error": f"Write failed: {exc}"}
            diff = "".join(difflib.unified_diff(
                before.splitlines(True), content.splitlines(True),
                fromfile=path_rel, tofile=path_rel))
            return {"ok": True, "path": path_rel, "checkpoint": checkpoint_id,
                    "diff": diff[:_DIFF_PREVIEW]}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def write_file(args: dict) -> dict:
        return _do_write(str(args["path"]), str(args.get("content", "")))

    def write_file_checked(args: dict) -> dict:
        expected = str(args.get("expected_sha256", ""))
        if not expected:
            return {"ok": False, "error": "expected_sha256 is required."}
        try:
            target = ctx.resolve(args["path"])
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        old_bytes = target.read_bytes() if target.exists() else None
        current = hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None
        if (expected == "__absent__" and old_bytes is not None) or (
            expected != "__absent__" and current != expected
        ):
            return {"ok": False, "error": (
                "File changed or does not match expected SHA-256. "
                "Read/fingerprint it again before writing; no write occurred.")}
        return _do_write(str(args["path"]), str(args.get("content", "")))

    def edit_file(args: dict) -> dict:
        path_rel = str(args["path"])
        old = str(args.get("old_text", ""))
        new = str(args.get("new_text", ""))
        if not old:
            return {"ok": False, "error": "old_text must not be empty."}
        try:
            target = ctx.resolve(path_rel)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not target.is_file():
            return {"ok": False, "error": f"Not a file: {path_rel!r}"}
        try:
            text = target.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        # Match agent_core: read_text normalizes CRLF; retry with the file's
        # existing Windows newline convention when the plain match fails.
        if text.count(old) == 0 and "\r\n" in text:
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        if text.count(old) != 1:
            return {"ok": False, "error": (
                "old_text must match exactly once. Read the file and retry.")}
        return _do_write(path_rel, text.replace(old, new, 1))

    def restore_change(args: dict) -> dict:
        try:
            message = ctx.restore(str(args.get("checkpoint_id", "")))
        except (ValueError, PermissionError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": message}

    def list_dir(args: dict) -> dict:
        rel = str(args.get("path", ".") or ".")
        show_hidden = bool(args.get("show_hidden", False))
        try:
            target = ctx.resolve(rel)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not target.is_dir():
            return {"ok": False, "error": f"Not a directory: {rel!r}"}
        try:
            entries = sorted(
                p.name + ("/" if p.is_dir() else "")
                for p in target.iterdir()
                if show_hidden or not p.name.startswith(".")
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": rel, "entries": entries[:500]}

    def mkdir(args: dict) -> dict:
        try:
            target = ctx.resolve(args["path"])
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": args["path"]}

    def delete_path(args: dict) -> dict:
        try:
            target = ctx.resolve(args["path"])
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if target == ctx.project_root:
            return {"ok": False, "error": "Refusing to delete the project root itself."}
        if not target.exists() and not target.is_symlink():
            return {"ok": False, "error": f"No such path: {args['path']!r}"}
        import shutil
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": args["path"], "deleted": True}

    def grep_search(args: dict) -> dict:
        query = str(args.get("query", ""))
        rel = str(args.get("path", ".") or ".")
        try:
            max_results = int(args.get("max_results", 50))
        except (TypeError, ValueError):
            max_results = 50
        max_results = max(1, min(max_results, 200))
        try:
            pattern = re.compile(query)
        except re.error as exc:
            return {"ok": False, "error": f"Invalid regex: {exc}"}
        try:
            base = ctx.resolve(rel)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        if not base.is_dir():
            return {"ok": False, "error": f"Not a directory: {rel!r}"}
        hits: list[str] = []
        from pathlib import Path as _Path
        for directory, folders, files in os.walk(base, followlinks=False):
            folders[:] = sorted(
                d for d in folders
                if d not in _SKIP_DIRS and not (_Path(directory, d).is_symlink())
            )
            for name in sorted(files):
                if len(hits) >= max_results:
                    break
                p = _Path(directory) / name
                if p.is_symlink():
                    continue
                try:
                    if p.stat().st_size > _GREP_FILE_LIMIT:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        relp = p.relative_to(ctx.project_root).as_posix()
                        hits.append(f"{relp}:{lineno}:{line.strip()[:200]}")
                        if len(hits) >= max_results:
                            break
            if len(hits) >= max_results:
                break
        return {"ok": True, "query": query, "hits": hits,
                "truncated": len(hits) >= max_results}

    tools = [
        ("read_file", "Read a UTF-8 text file inside the project (250 KB cap).",
         {"path": "Relative file path inside the project"}, read_file),
        ("file_fingerprint", "Report whether a file exists, its byte size and SHA-256. Use immediately before write_file_checked.",
         {"path": "Relative file path"}, file_fingerprint),
        ("write_file", "Create or replace a project text file (500 KB cap). Original bytes are checkpointed; returns a diff preview.",
         {"path": "Relative file path", "content": "Complete new contents"}, write_file),
        ("write_file_checked", "Write only if the current SHA-256 matches expected_sha256 (or '__absent__' when the file must not exist).",
         {"path": "Relative file path", "content": "Complete new contents",
          "expected_sha256": "SHA-256 from file_fingerprint, or __absent__"}, write_file_checked),
        ("edit_file", "Replace one exact unique text occurrence in an existing file; original is checkpointed.",
         {"path": "Relative file path", "old_text": "Exact unique text to replace",
          "new_text": "Replacement text"}, edit_file),
        ("restore_change", "Undo one checkpointed write by its checkpoint id. Refuses if the file changed since the edit.",
         {"checkpoint_id": "Checkpoint id returned by write_file/edit_file"}, restore_change),
        ("list_dir", "List directory entries inside the project (dotfiles hidden unless show_hidden).",
         {"path": {"type": "string", "description": "Relative directory path", "required": False, "default": "."},
          "show_hidden": {"type": "boolean", "description": "Include dotfiles", "required": False, "default": False}},
         list_dir),
        ("mkdir", "Create a directory (including parents) inside the project.",
         {"path": "Relative directory path"}, mkdir),
        ("delete_path", "Delete a file or directory inside the project. Never the project root itself.",
         {"path": "Relative path to delete"}, delete_path),
        ("grep_search", "Regex search across project text files; returns path:line matches, bounded.",
         {"query": "Regular expression to search for",
          "path": {"type": "string", "description": "Relative directory to search", "required": False, "default": "."},
          "max_results": {"type": "integer", "description": "Max matches (1-200)", "required": False, "default": 50}},
         grep_search),
    ]
    for name, description, properties, handler in tools:
        registry.register(name, description, function_schema(name, description, properties), handler)
