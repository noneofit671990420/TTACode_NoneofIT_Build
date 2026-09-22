"""Discover and parse ``SKILL.md`` skill packs.

A skill is a directory containing a ``SKILL.md`` file with YAML-ish
frontmatter::

    ---
    name: example-hello
    description: Says hello; demonstrates the skill format.
    version: 0.1.0
    ---
    # Hello skill
    ...markdown instructions for the model...

Frontmatter is parsed with a tiny built-in parser (``name: value`` lines
only, no new dependencies). Malformed frontmatter never raises: the
skill is returned with just its name and body, and ``meta`` stays empty.

Default search dirs: ``~/.ttacode/skills`` and ``./skills`` (repo-local).
"""

from __future__ import annotations

import os
from pathlib import Path


def _home() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    return Path(os.path.expanduser("~"))


def default_skill_dirs() -> list[Path]:
    return [_home() / ".ttacode" / "skills", Path.cwd() / "skills"]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` frontmatter from a markdown document.

    Returns ``(meta, body)``. When no valid frontmatter block is found,
    ``meta`` is ``{}`` and ``body`` is the original text.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and not key.startswith("#"):
            meta[key] = value
    if end is None:
        return {}, text  # unterminated block: treat as no frontmatter
    body = "\n".join(lines[end + 1 :])
    return meta, body


def load_skill(skill_file: str | Path) -> dict | None:
    """Load one ``SKILL.md`` file. Returns None when unreadable."""
    path = Path(skill_file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = parse_frontmatter(text)
    name = meta.get("name") or path.parent.name
    return {
        "name": name,
        "description": meta.get("description", ""),
        "version": meta.get("version", ""),
        "meta": meta,
        "body": body.strip(),
        "path": str(path),
    }


def discover_skills(dirs: list[str | Path] | None = None) -> list[dict]:
    """Find all ``SKILL.md`` files under the given dirs (non-recursive walk
    limited to two levels: ``<dir>/<skill-name>/SKILL.md``). Never raises."""
    search = [Path(d) for d in dirs] if dirs is not None else default_skill_dirs()
    skills: list[dict] = []
    for base in search:
        try:
            candidates = sorted(base.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            skill_file = candidate / "SKILL.md"
            if candidate.is_dir() and skill_file.is_file():
                try:
                    skill = load_skill(skill_file)
                except Exception:
                    continue
                if skill is not None:
                    skills.append(skill)
    skills.sort(key=lambda s: s["name"])
    return skills
