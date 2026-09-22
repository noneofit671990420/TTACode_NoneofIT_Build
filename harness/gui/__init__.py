"""TTACode desktop GUI: one-click local AI chat for average users.

Double-click ``ttacode.exe`` → warm model → chat. No flags to remember.
Every message runs the real harness agent loop (shell, files, web, MCP
tools), images ride along to vision-capable models via Ollama's
``/api/chat`` ``images`` field, and a one-click button installs a vision
model when none is present.

Qt-dependent modules (``worker``, ``main_window``, ``app``) are only
imported when the GUI actually launches; the pure helpers here
(``markdown``, ``images``, ``theme``, ``models``) stay importable — and
testable — without Qt.
"""

__all__ = ["markdown", "images", "theme", "models"]
