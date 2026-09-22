"""Shared context passed to every tool: project root, config, checkpoints.

``ToolContext`` is the one object tool authors receive. It carries:

* ``project_root`` — the directory all file/shell tools are scoped to.
* ``config`` — the harness config dict (timeouts, limits, user settings).
* checkpointing — snapshot-before-write semantics ported from
  ``agent_core.ProjectTools._write_file``: every mutation stores the
  original bytes plus a record, so ``restore_change`` can undo it.

Checkpoints live under ``<project>/.ttacode/checkpoints/<uuid>/`` with
``original`` (previous bytes, absent when the file is new) and
``record.json`` (path, existed flag, sha256 of the new content).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Filenames the agent must never read or write through file tools.
# Ported from agent_core.ProjectTools.path().
_SECRET_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "tokens.json",
}
_SECRET_SUFFIXES = {".pem", ".key", ".pfx"}
# Metadata dirs excluded from agent edits.
_EXCLUDED_PARTS = {".git", ".ttacode", ".talktoai-code"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class ToolContext:
    project_root: Path
    config: dict = field(default_factory=dict)
    checkpoint_dirname: str = ".ttacode"

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")
        self.changes: list[dict] = []

    # -- path containment -------------------------------------------------
    def resolve(self, rel: str | Path) -> Path:
        """Resolve ``rel`` inside the project root.

        Raises ``ValueError`` when the path escapes the root or points at
        internal metadata, and ``PermissionError`` for credential-looking
        files — mirroring ``agent_core.ProjectTools.path()``.
        """
        target = (self.project_root / str(rel)).resolve()
        if target != self.project_root and self.project_root not in target.parents:
            raise ValueError(f"Path escapes the project root: {rel!r}")
        rel_parts = target.relative_to(self.project_root).parts
        if any(part in _EXCLUDED_PARTS for part in rel_parts):
            raise ValueError("Internal project metadata is excluded from editing.")
        lowered = target.name.lower()
        if (
            lowered in _SECRET_NAMES
            or lowered.startswith(".env.")
            or target.suffix.lower() in _SECRET_SUFFIXES
        ):
            raise PermissionError(
                "Credential/config-secret files are excluded from agent file tools."
            )
        return target

    # -- checkpoints -------------------------------------------------------
    @property
    def checkpoint_base(self) -> Path:
        return self.project_root / self.checkpoint_dirname / "checkpoints"

    def snapshot(self, path: Path, old_bytes: bytes | None, new_bytes: bytes) -> str:
        """Store a checkpoint for a write. Returns the checkpoint id."""
        checkpoint_id = uuid.uuid4().hex
        folder = self.checkpoint_base / checkpoint_id
        folder.mkdir(parents=True, exist_ok=True)
        if old_bytes is not None:
            (folder / "original").write_bytes(old_bytes)
        record = {
            "path": str(path),
            "existed": old_bytes is not None,
            "new_sha256": _sha256(new_bytes),
        }
        (folder / "record.json").write_text(json.dumps(record), encoding="utf-8")
        try:
            rel = str(path.relative_to(self.project_root))
        except ValueError:
            rel = str(path)
        self.changes.append(
            {"path": rel, "checkpoint": checkpoint_id, "restored": False}
        )
        return checkpoint_id

    def discard_checkpoint(self, checkpoint_id: str) -> None:
        """Drop a checkpoint whose write never happened.

        Used when snapshotting before mutation and the write then fails,
        so history never claims a change that didn't occur.
        """
        folder = self.checkpoint_base / checkpoint_id
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self.changes = [
            c for c in self.changes if c.get("checkpoint") != checkpoint_id
        ]

    def restore(self, checkpoint_id: str) -> str:
        """Undo one checkpointed write. Refuses when the file changed since."""
        if not checkpoint_id or not checkpoint_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid checkpoint id: {checkpoint_id!r}")
        folder = self.checkpoint_base / checkpoint_id
        record_file = folder / "record.json"
        if not record_file.is_file():
            raise ValueError(f"Unknown checkpoint: {checkpoint_id!r}")
        try:
            record = json.loads(record_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"Checkpoint record is corrupt: {exc}") from exc
        path = Path(record["path"])
        # Keep the restore scoped to this project.
        resolved = path.resolve()
        if resolved != self.project_root and self.project_root not in resolved.parents:
            raise ValueError("Checkpoint points outside the project root; refusing.")
        current = resolved.read_bytes() if resolved.exists() else None
        if current is None or _sha256(current) != record.get("new_sha256"):
            raise ValueError(
                "File changed since this edit. Restore manually to preserve newer work."
            )
        if record.get("existed"):
            resolved.write_bytes((folder / "original").read_bytes())
        else:
            resolved.unlink()
        for change in self.changes:
            if change.get("checkpoint") == checkpoint_id:
                change["restored"] = True
        return f"Restored {record['path']} to its pre-edit state."

    def changes_since(self, count: int) -> list[dict]:
        """Change records appended after ``count`` (for verification nudges)."""
        return self.changes[count:]
