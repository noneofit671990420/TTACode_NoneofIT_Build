"""First-run precheck dialog: guided, announced setup.

Runs BEFORE the main window. Each step is announced as it happens
(Ollama → models → your machine), and every failure state offers a
one-click guided fix instead of stranding the user in an empty window.

Accepted → ``selected_model`` is the engine to warm up.
Rejected (window closed) → the app exits; nothing half-loaded.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from harness import __version__
from harness.gui import theme
from harness.gui.models import find_vision_model
from harness.gui.preflight import (
    OLLAMA_DOWNLOAD_URL,
    collect_preflight,
    diagnostics_text,
    start_ollama_server,
)
from harness.gui.worker import PullWorker, auto_cleanup

_STEP_LABELS = (
    ("ollama", "Ollama — the engine that runs models"),
    ("models", "Installed models"),
    ("specs", "Your machine"),
)


class PreflightWorker(QThread):
    step = Signal(str, str, str)  # name, status, detail
    done = Signal(dict)

    def run(self) -> None:  # noqa: D102
        self.done.emit(collect_preflight(on_step=self.step.emit))


class PrecheckDialog(QDialog):
    """Modal guided precheck. See module docstring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TTACode — first-run check")
        self.setModal(True)
        self.resize(560, 520)

        self.selected_model: str | None = None
        self._result: dict = {}
        self._installing = False
        self._pull_worker: PullWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Getting TTACode ready")
        title.setObjectName("title")
        layout.addWidget(title)
        sub = QLabel(
            "Checking everything out loud, so you're never left guessing."
        )
        sub.setObjectName("subtitle")
        layout.addWidget(sub)

        self._steps = QListWidget()
        self._steps.setMaximumHeight(120)
        self._step_items: dict[str, QListWidgetItem] = {}
        for key, label in _STEP_LABELS:
            item = QListWidgetItem(f"○  {label} — waiting…")
            self._steps.addItem(item)
            self._step_items[key] = item
        layout.addWidget(self._steps)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setOpenExternalLinks(True)
        self._body.setText("Starting checks…")
        layout.addWidget(self._body, 1)

        self._model_row = QWidget()
        model_layout = QHBoxLayout(self._model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(QLabel("Start with:"))
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(280)
        model_layout.addWidget(self._model_combo, 1)
        self._model_row.setVisible(False)
        layout.addWidget(self._model_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._action_btn = QPushButton()
        self._action_btn.setVisible(False)
        self._action_btn.clicked.connect(self._on_action)
        btn_row.addWidget(self._action_btn)
        self._retry_btn = QPushButton("Retry check")
        self._retry_btn.setVisible(False)
        self._retry_btn.clicked.connect(self._run_checks)
        btn_row.addWidget(self._retry_btn)
        btn_row.addStretch(1)
        self._diag_btn = QPushButton("Copy diagnostics")
        self._diag_btn.setVisible(False)
        self._diag_btn.clicked.connect(self._copy_diagnostics)
        btn_row.addWidget(self._diag_btn)
        self._continue_btn = QPushButton("Continue →")
        self._continue_btn.setEnabled(False)
        self._continue_btn.setDefault(True)
        self._continue_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._continue_btn)
        layout.addLayout(btn_row)

        self._action_mode: str | None = None  # download-ollama | start-ollama | install-model
        self._action_model: str | None = None
        self._run_checks()

    # -- checks -----------------------------------------------------------
    def _run_checks(self) -> None:
        for key, label in _STEP_LABELS:
            self._step_items[key].setText(f"○  {label} — waiting…")
        self._body.setText("Starting checks…")
        self._continue_btn.setEnabled(False)
        self._action_btn.setVisible(False)
        self._retry_btn.setVisible(False)
        self._diag_btn.setVisible(False)
        self._model_row.setVisible(False)
        worker = PreflightWorker(self)
        auto_cleanup(worker)
        worker.step.connect(self._on_step)
        worker.done.connect(self._on_preflight_done)
        worker.start()

    def _on_step(self, name: str, status: str, detail: str) -> None:
        label = dict(_STEP_LABELS).get(name, name)
        icon = {"running": "◌", "ok": "✓", "fail": "✕"}.get(status, "○")
        self._step_items[name].setText(f"{icon}  {label} — {detail}")

    def _on_preflight_done(self, result: dict) -> None:
        self._result = result
        self._diag_btn.setVisible(True)
        ollama = result.get("ollama", {})
        models: list = result.get("models", [])
        if not ollama.get("ok"):
            self._show_no_ollama(ollama)
        elif not models:
            self._show_no_models(result)
        else:
            self._show_ready(result)

    # -- states -----------------------------------------------------------
    def _show_no_ollama(self, ollama: dict) -> None:
        hint = ollama.get("hint", "")
        if ollama.get("installed"):
            self._body.setText(
                f"<p>⚠ {hint}</p>"
                "<p>TTACode talks to Ollama to run models locally. "
                "I can try starting it for you:</p>"
            )
            self._set_action("start-ollama", "▶ Start Ollama for me")
        else:
            self._body.setText(
                f"<p>⚠ {hint}</p>"
                "<p>It's free and takes a minute:</p>"
                "<p>1. Install Ollama<br>"
                "2. Come back here and hit <b>Retry check</b></p>"
            )
            self._set_action("download-ollama", "⬇ Download Ollama")
        self._retry_btn.setVisible(True)

    def _show_no_models(self, result: dict) -> None:
        recs = result.get("recs", {})
        chat = (recs.get("chat") or {}).get("name", "qwen2.5-coder:7b")
        spec_line = result.get("spec_line", "")
        self._body.setText(
            "<p>📦 <b>No models installed yet</b> — nothing to chat with.</p>"
            f"<p>{spec_line}</p>"
            f"<p>For your machine I recommend starting with "
            f"<code>{chat}</code>. One click, no terminal:</p>"
        )
        self._set_action("install-model", f"⬇ Install {chat}", model=chat)
        self._retry_btn.setVisible(True)

    def _show_ready(self, result: dict) -> None:
        models: list = result["models"]
        recs = result.get("recs", {})
        installed = {m["name"] for m in models}
        spec_line = result.get("spec_line", "")

        lines = [f"<p>✓ <b>{spec_line}</b></p>", "<p><b>Best engines for this machine:</b></p><ul>"]
        for key, label in (("chat", "Chat / coding"), ("vision", "Vision (images)")):
            rec = recs.get(key) or {}
            name = rec.get("name", "—")
            why = rec.get("why", "")
            mark = "✓ installed" if name in installed else "not installed"
            lines.append(f"<li>{label}: <code>{name}</code> — {mark}<br><small>{why}</small></li>")
        lines.append("</ul>")
        lines.append(
            "<p><small>Warming up the model will spike your GPU briefly — "
            "that's normal, it's loading into VRAM.</small></p>"
        )
        if not find_vision_model([m["name"] for m in models]):
            vision = (recs.get("vision") or {}).get("name")
            if vision and vision not in installed:
                lines.append(
                    f"<p>💡 Want image chat too? "
                    f'<a href="ttacode://install-vision">Install {vision}</a> '
                    "after setup (one click).</p>"
                )
        self._body.setText("".join(lines))

        self._model_combo.clear()
        for m in models:
            self._model_combo.addItem(m["label"], m["name"])
        want = result.get("startup_model")
        if want:
            idx = self._model_combo.findData(want)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        self._model_row.setVisible(True)

        # Offer the recommended-but-missing engine as a one-click install.
        for key in ("chat", "vision"):
            name = (recs.get(key) or {}).get("name")
            if name and name not in installed:
                self._set_action("install-model", f"⬇ Install {name}", model=name)
                break
        else:
            self._action_btn.setVisible(False)

        self._continue_btn.setEnabled(True)

    # -- actions ----------------------------------------------------------
    def _set_action(self, mode: str, text: str, model: str | None = None) -> None:
        self._action_mode = mode
        self._action_model = model
        self._action_btn.setText(text)
        self._action_btn.setVisible(True)
        self._action_btn.setEnabled(True)

    def _on_action(self) -> None:
        mode = self._action_mode
        if mode == "download-ollama":
            QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        elif mode == "start-ollama":
            self._action_btn.setEnabled(False)
            self._body.setText("<p>Starting Ollama…</p>")
            if start_ollama_server():
                self._body.setText(
                    "<p>Ollama is starting — give it a few seconds, "
                    "then hit <b>Retry check</b>.</p>"
                )
            else:
                self._body.setText(
                    "<p>Couldn't start Ollama automatically. "
                    "Start the Ollama app (or run <code>ollama serve</code>), "
                    "then hit <b>Retry check</b>.</p>"
                )
            self._retry_btn.setVisible(True)
        elif mode == "install-model" and self._action_model:
            self._install_model(self._action_model)

    def _install_model(self, model: str) -> None:
        if self._installing:
            return
        self._installing = True
        self._action_btn.setEnabled(False)
        self._retry_btn.setVisible(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate until we get numbers
        self._body.setText(
            f"<p>⬇ Downloading <code>{model}</code>…<br>"
            "<small>This can take a few minutes on first install.</small></p>"
        )
        worker = auto_cleanup(PullWorker(model, parent=self))
        self._pull_worker = worker
        worker.progress.connect(self._on_pull_progress)
        worker.done.connect(lambda m: self._on_pull_done(m, True))
        worker.failed.connect(lambda m, e: self._on_pull_done(m, False, e))
        worker.start()

    def _on_pull_progress(self, pct: float, detail: str) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(int(pct * 100))
        self._progress.setFormat(f"{detail} — %p%")

    def _on_pull_done(self, model: str, ok: bool, error: str = "") -> None:
        self._installing = False
        self._progress.setVisible(False)
        if ok:
            self._body.setText(
                f"<p>✓ <code>{model}</code> installed.</p>"
                "<p>Re-running checks…</p>"
            )
            self._run_checks()
        else:
            self._body.setText(
                f"<p>⚠ Install failed: {error}</p>"
                f"<p>Or install it yourself: <code>ollama pull {model}</code> "
                "then hit <b>Retry check</b>.</p>"
            )
            self._retry_btn.setVisible(True)
            self._action_btn.setEnabled(True)

    def _copy_diagnostics(self) -> None:
        text = diagnostics_text(self._result, __version__)
        QApplication.clipboard().setText(text)
        self._diag_btn.setText("Copied ✓")
        self._diag_btn.setEnabled(False)

    # -- result -----------------------------------------------------------
    def accept(self) -> None:  # noqa: D102
        idx = self._model_combo.currentIndex()
        data = self._model_combo.itemData(idx) if self._model_row.isVisible() else None
        self.selected_model = data or self._result.get("startup_model")
        super().accept()


def apply_theme(dialog: QDialog) -> None:
    """Apply the TTACode stylesheet to the precheck dialog."""
    dialog.setStyleSheet(theme.QSS)
