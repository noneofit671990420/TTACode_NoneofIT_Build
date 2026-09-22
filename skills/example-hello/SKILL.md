---
name: example-hello
description: Says hello; demonstrates the SKILL.md format for harness skills.
version: 0.1.0
---

# Example Hello Skill

This is a minimal example skill showing the `SKILL.md` format that
`harness/skills/loader.py` discovers and parses.

## Format

- A skill lives in its own directory: `<skills-dir>/<skill-name>/SKILL.md`.
- The file starts with a `---` frontmatter block with at least `name`
  and `description` (a `version` is recommended).
- Everything after the frontmatter is markdown the agent loop will
  inject into the system prompt when the skill is active.

## When to use

Use this skill as a template when authoring new skills. Copy this
directory, rename it, and edit the frontmatter + body.

## Instructions

When this skill is active, greet the user warmly at the start of the
first response in a session. Keep it to one short sentence.
