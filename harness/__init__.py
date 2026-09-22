"""TTACode headless harness — modular, tiny, local-first AI agent core.

Vision
------
The ``harness`` package is the OpenHarness-style refactor of TalkToAi Code:
a small, headless, dependency-free (stdlib-only) core that can run an AI
agent loop on any system (within reason), independent of the Qt desktop UI.

Design principles:

* **Stdlib-first.** No heavy dependencies in the core. If it is not in the
  Python standard library, it does not belong here. (Mirrors the plain
  style of the original ``routing.py``.)
* **Modular.** Each concern is its own subpackage: ``models`` (model
  discovery), ``transports`` (how we talk to a model), ``tools`` (local
  tool registry), ``mcp`` (Model Context Protocol client), ``skills``
  (markdown skill loader), ``agent`` (the headless agent loop).
* **Headless.** Everything is drivable from ``python -m harness``. The Qt
  studio UI (``studio.py``) is a future optional plugin, not a requirement.
* **Local-first.** The model scanner reads the model stores already on the
  user's PC (Ollama, LM Studio, ...) — no auto-downloads, ever.
* **Honest stubs.** Anything not yet implemented raises
  ``NotImplementedError`` with a pointer to the roadmap instead of faking
  behavior.

Layout::

    harness/
        cli.py          argparse CLI (init, models, run, mcp, skills)
        models/         disk + live model discovery, default-model heuristic
        transports/     thin model transports (Ollama via urllib)
        tools/          minimal local tool registry
        mcp/            MCP client skeleton (stdio / SSE planned)
        skills/         SKILL.md loader with tiny frontmatter parser
        agent/          headless agent loop skeleton

Roadmap: ``docs/harness-roadmap.md`` (repo root).
"""

__version__ = "0.2.3"
