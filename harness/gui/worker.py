"""Background workers: model setup, chat turns, and ``ollama pull``.

All blocking work (model warm-up can take minutes, chat turns even
longer) runs off the GUI thread; results come back via signals.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal


def auto_cleanup(worker: QThread) -> QThread:
    """Schedule a finished worker for deletion.

    Workers are parented to the main window, so without this every chat
    turn / setup / pull leaks a finished QThread as a permanent child —
    the session gets slower the longer it runs. Returns the worker for
    chaining.
    """
    worker.finished.connect(worker.deleteLater)
    return worker


class SetupWorker(QThread):
    """Build (or rebuild) the agent session off the GUI thread."""

    progress = Signal(str)
    done = Signal(object)  # harness.session.Session
    failed = Signal(str)  # human-readable error

    def __init__(
        self,
        model: str | None = None,
        project: str | None = None,
        config: dict | None = None,
        old_session=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._project = project
        self._config = config
        self._old_session = old_session

    def run(self) -> None:
        from harness.models.loader import ModelLoadError
        from harness.session import SessionError, build_session, close_session

        try:
            session = build_session(
                model=self._model,
                project=self._project,
                config=self._config,
                on_progress=self.progress.emit,
            )
        except (SessionError, ModelLoadError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # never strand the UI on setup
            self.failed.emit(f"Unexpected setup error: {exc}")
            return
        if self._old_session is not None:
            close_session(self._old_session)
        self.done.emit(session)


class ChatWorker(QThread):
    """Run one agent turn (tools included) off the GUI thread."""

    done = Signal(dict)  # AgentLoop result dict

    def __init__(self, session, prompt: str, images: list[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._session = session
        self._prompt = prompt
        self._images = images

    def run(self) -> None:
        try:
            result = self._session.loop.chat_turn(self._prompt, self._images or None)
        except Exception as exc:  # chat_turn is honest; this is a bug guard
            result = {
                "result": f"Unexpected error during this turn: {exc}",
                "steps": 0,
                "tool_calls": [],
                "stopped_reason": "error",
                "model": self._session.loaded.name,
            }
        self.done.emit(result)


class PullWorker(QThread):
    """One-click model install: ``ollama pull <model>`` with progress."""

    progress = Signal(int, str)  # percent (0-100, -1 = indeterminate), line
    done = Signal(bool, str)  # ok, message

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def run(self) -> None:
        if not shutil.which("ollama"):
            self.done.emit(
                False,
                "Ollama was not found. Install it first:\n"
                "https://ollama.com/download/windows",
            )
            return
        self.progress.emit(-1, f"Contacting Ollama for {self._model}…")
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", self._model],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
        except Exception as exc:
            self.done.emit(False, f"Could not start ollama pull: {exc}")
            return
        percent = -1
        last_line = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            last_line = line.strip()
            match = re.search(r"(\d+)%", last_line)
            if match:
                percent = max(percent, min(100, int(match.group(1))))
            self.progress.emit(percent, last_line[:120])
        code = proc.wait()
        if code == 0:
            self.done.emit(True, f"{self._model} installed.")
        else:
            self.done.emit(
                False,
                f"Install failed (exit {code}). Last output:\n{last_line}\n\n"
                f"You can also run this yourself:\nollama pull {self._model}",
            )
