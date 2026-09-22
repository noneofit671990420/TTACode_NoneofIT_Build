"""Headless agent loop skeleton.

This is an honest stub: the real loop (ported from the original
``agent_core.py`` tool-calling loop, adapted to the harness
``ToolRegistry`` + transports + skills) is roadmap work — see
``docs/harness-roadmap.md``. Nothing here pretends to run.
"""

from __future__ import annotations


class AgentLoop:
    """Configuration holder for the future headless agent loop."""

    def __init__(
        self,
        transport,
        model: str,
        tools=None,
        skills: list[dict] | None = None,
        mcp=None,
        project: str | None = None,
        max_steps: int = 25,
    ) -> None:
        self.transport = transport
        self.model = model
        self.tools = tools
        self.skills = skills or []
        self.mcp = mcp
        self.project = project
        self.max_steps = max_steps

    def build_system_prompt(self) -> str:
        """Draft system prompt: base instructions + active skill bodies."""
        parts = [
            "You are a careful local coding assistant. Inspect before "
            "proposing edits, scope work to the project, favor small "
            "testable steps, and never claim a file was changed unless "
            "a tool actually changed it."
        ]
        for skill in self.skills:
            body = skill.get("body", "").strip()
            if body:
                parts.append(f"\n## Skill: {skill.get('name', 'unnamed')}\n{body}")
        return "\n".join(parts)

    def run(self, prompt: str):
        raise NotImplementedError(
            "agent loop not yet implemented — see docs/harness-roadmap.md "
            "for the plan to port the agent_core tool loop to this harness."
        )
