"""TTACode chat window: one window, chat bubbles, drag-and-drop images.

No flags, no terminal. Type, attach an image, hit SEND — every message
runs the real harness agent loop (shell, files, web, MCP tools).
"""

from __future__ import annotations

import itertools
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QTextDocument

from harness.cli import load_config, save_config
from harness.gui import images as image_helpers
from harness.gui import markdown, theme
from harness.gui.models import find_vision_model, list_gui_models
from harness.gui.worker import (
    ChatWorker,
    PullWorker,
    SetupWorker,
    auto_cleanup,
)
from harness.models import (
    DEFAULT_VISION_MODEL,
    is_vision_model,
    vision_supports_tools,
)


class ChatView(QTextBrowser):
    """Chat log with in-memory ``img://`` thumbnails."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._images: dict[str, QImage] = {}
        self.setOpenLinks(False)

    def add_image(self, key: str, image: QImage) -> None:
        self._images[key] = image

    def loadResource(self, resource_type, url: QUrl):
        if resource_type == QTextDocument.ResourceType.ImageResource:
            # ``img:<key>`` thumbnails live in memory, not on disk.
            key = url.path() or url.host()
            image = self._images.get(key)
            if image is not None:
                return image
        return super().loadResource(resource_type, url)

    def append_html(self, fragment: str) -> None:
        self.append(
            f"<html><head>{theme.CHAT_CSS}</head><body>{fragment}</body></html>"
        )
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class MainWindow(QMainWindow):
    def __init__(self, model: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("TTACode")
        self.resize(1000, 740)
        self.setAcceptDrops(True)

        self._config: dict = load_config()
        self._session = None
        self._pending_images: list[Path] = []
        self._pending_send: tuple[str, list[Path]] | None = None
        self._busy = False
        self._img_ids = itertools.count(1)
        self._pulling = False
        self._pull_model: str | None = None
        self._initial_model = model

        self._build_ui()
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(450)
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._thinking_dots = 0

        self._start_setup(model=self._initial_model)

    # -- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 14, 18, 10)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("TTACODE")
        title.setObjectName("title")
        subtitle = QLabel("local ai · runs on your pc")
        subtitle.setObjectName("subtitle")
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box)
        top.addStretch(1)

        self._model_combo = _ModelCombo(self)
        top.addWidget(self._model_combo)
        self._model_combo.activated.connect(self._on_model_activated)

        self._folder_btn = QToolButton()
        self._folder_btn.setText("Project")
        self._folder_btn.setToolTip("Working folder for file/shell tools")
        self._folder_btn.clicked.connect(self._pick_folder)
        top.addWidget(self._folder_btn)

        new_btn = QToolButton()
        new_btn.setText("New chat")
        new_btn.clicked.connect(self._new_chat)
        top.addWidget(new_btn)
        layout.addLayout(top)

        self._chat = ChatView()
        self._chat.setObjectName("chat")
        self._chat.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self._chat, 1)

        self._strip = QWidget()
        strip_layout = QHBoxLayout(self._strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(8)
        strip_layout.addStretch(1)
        self._strip.setVisible(False)
        layout.addWidget(self._strip)

        row = QHBoxLayout()
        attach = QToolButton()
        attach.setObjectName("attach")
        attach.setText("+")
        attach.setToolTip("Attach an image (or drag & drop one)")
        attach.clicked.connect(self._attach_dialog)
        row.addWidget(attach)

        self._input = QTextEdit()
        self._input.setObjectName("input")
        self._input.setPlaceholderText(
            "Ask anything — or drop an image and ask about it…"
        )
        self._input.setFixedHeight(78)
        self._input.installEventFilter(self)
        row.addWidget(self._input, 1)

        self._send_btn = QPushButton("SEND")
        self._send_btn.setObjectName("send")
        self._send_btn.clicked.connect(self._on_send_clicked)
        row.addWidget(self._send_btn)
        layout.addLayout(row)

        status = QStatusBar()
        self.setStatusBar(status)
        dot = QLabel("●")
        dot.setObjectName("statusdot")
        status.addWidget(dot)
        self._status_label = QLabel("Starting…")
        status.addWidget(self._status_label, 1)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedWidth(220)
        status.addPermanentWidget(self._progress)

    # -- setup ------------------------------------------------------------
    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._model_combo.setEnabled(not busy)
        if status:
            self._status_label.setText(status)

    def _start_setup(self, model: str | None = None, project: str | None = None) -> None:
        self._set_busy(True, "Looking for models…")
        self._setup_worker = auto_cleanup(SetupWorker(
            model=model,
            project=project,
            config=self._config,
            old_session=self._session,
            parent=self,
        ))
        self._setup_worker.progress.connect(self._status_label.setText)
        self._setup_worker.done.connect(self._on_setup_done)
        self._setup_worker.failed.connect(self._on_setup_failed)
        self._setup_worker.start()

    def _on_setup_done(self, session) -> None:
        first = self._session is None
        self._session = session
        self._refresh_models(session.loaded.name)
        self._folder_btn.setToolTip(f"Working folder:\n{session.project_path}")
        self._set_busy(False, f"Ready — {session.loaded.name}")
        if first:
            self._welcome()
        if self._pending_send is not None:
            text, paths = self._pending_send
            self._pending_send = None
            self._send_now(text, paths)

    def _on_setup_failed(self, message: str) -> None:
        self._set_busy(False, "Setup failed")
        self._chat.append_html(
            '<div class="msg error"><div class="who">TTACODE</div>'
            f"<div>{markdown.render(message)}</div>"
            '<p><a href="ttacode://retry-setup">Retry</a></p></div>'
        )

    def _welcome(self) -> None:
        assert self._session is not None
        project = self._session.project_path
        self._chat.append_html(
            '<div class="msg assistant"><div class="who">TTACODE</div>'
            "<div>"
            f"<p>Ready. I run tools for you — files, shell, web. "
            f"Working folder: <code>{project}</code></p>"
            "<p>Drop an image anytime to use vision.</p>"
            "</div></div>"
        )

    # -- models ------------------------------------------------------------
    def _refresh_models(self, current: str) -> None:
        combo = self._model_combo
        combo.blockSignals(True)
        combo.clear()
        entries = list_gui_models()
        for entry in entries:
            combo.addItem(entry["label"], entry["name"])
        if not entries:
            # Never strand the user in front of an empty dropdown: the
            # precheck dialog guarantees models at launch, but if discovery
            # later comes back empty (Ollama stopped?), say so plainly and
            # point at the fix instead of showing a dead control.
            combo.addItem("No models found — restart the app", None)
            self._chat.append_html(
                '<div class="msg notice"><div class="who">TTACODE</div>'
                "<p>⚠ I can't see any installed models right now. "
                "Ollama may have stopped — start it and reopen TTACode, "
                "and the first-run check will guide you from there.</p>"
                "</div>"
            )
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _on_model_activated(self, index: int) -> None:
        name = self._model_combo.itemData(index)
        if not name or (self._session and name == self._session.loaded.name):
            return
        self._config["default_model"] = name
        save_config(self._config)
        self._chat.append_html(
            f'<div class="tools">switching to {markdown.render(name)}…</div>'
        )
        self._start_setup(model=name)

    # -- sending -----------------------------------------------------------
    def eventFilter(self, obj, event):  # Enter sends, Shift+Enter newline
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    def _on_send_clicked(self) -> None:
        if self._busy or self._session is None:
            return
        text = self._input.toPlainText().strip()
        paths = list(self._pending_images)
        if not text and not paths:
            return
        if paths and not is_vision_model(self._session.loaded.name):
            self._handle_non_vision_send(text, paths)
            return
        self._send_now(text, paths)

    def _handle_non_vision_send(self, text: str, paths: list[Path]) -> None:
        """Images attached but the current model can't see."""
        vision = find_vision_model()
        if vision is not None:
            self._pending_send = (text, paths)
            self._clear_pending_images()
            self._input.clear()
            self._chat.append_html(
                '<div class="tools">'
                f"switching to {vision['name']} for image vision…"
                "</div>"
            )
            self._config["default_model"] = vision["name"]
            save_config(self._config)
            self._start_setup(model=vision["name"])
            return
        # Nothing vision-capable installed: offer the one-click install.
        self._pending_send = (text, paths)
        model = self._config.get("vision_model") or DEFAULT_VISION_MODEL
        self._chat.append_html(
            self._vision_install_card_html(
                model,
                "This model can't see images. Install a vision model — "
                "one click, then I'll send your message automatically:",
            )
        )

    def _vision_install_card_html(self, model: str, intro: str) -> str:
        """One-click vision-model install card (no terminal needed)."""
        return (
            '<div class="msg notice"><div class="who">TTACODE</div>'
            f"<p>{intro}</p>"
            f'<p><a href="ttacode://install-vision">'
            f"⬇ Install {model}</a></p>"
            f"<p>Prefer the terminal? <code>ollama pull {model}</code></p>"
            "</div>"
        )

    def _send_now(self, text: str, paths: list[Path]) -> None:
        cid_images: list[tuple[str, str]] = []  # (cid, base64)
        thumbs: list[str] = []
        for path in paths:
            try:
                payload = image_helpers.encode_image_file(path)
            except ValueError as exc:
                QMessageBox.warning(self, "TTACode", str(exc))
                return
            cid = f"img{next(self._img_ids)}"
            image = QImage(str(path))
            self._chat.add_image(cid, image)
            thumbs.append(cid)
            cid_images.append((cid, payload))
        self._clear_pending_images()
        self._input.clear()

        fragment = '<div class="msg user"><div class="who">YOU</div>'
        for cid in thumbs:
            fragment += f'<img src="img:{cid}" width="280"><br>'
        fragment += f"<div>{markdown.render(text) if text else '<p><i>[image]</i></p>'}</div></div>"
        self._chat.append_html(fragment)

        self._set_busy(True)
        self._thinking_dots = 0
        self._thinking_timer.start()
        self._chat_worker = auto_cleanup(ChatWorker(
            self._session, text, [b64 for _cid, b64 in cid_images], parent=self
        ))
        self._chat_worker.done.connect(self._on_turn_done)
        self._chat_worker.start()

    def _tick_thinking(self) -> None:
        self._thinking_dots = (self._thinking_dots + 1) % 4
        self._status_label.setText("Thinking" + "." * self._thinking_dots)

    def _on_turn_done(self, result: dict) -> None:
        self._thinking_timer.stop()
        stopped = result.get("stopped_reason", "")
        css = "error" if stopped in ("transport_error", "error") else "assistant"
        self._chat.append_html(
            f'<div class="msg {css}"><div class="who">TTACODE</div>'
            f"<div>{markdown.render(result.get('result') or '')}</div></div>"
        )
        calls = result.get("tool_calls") or []
        if calls:
            names = [c.get("name", "?") for c in calls]
            shown = ", ".join(names[:8])
            extra = f" +{len(names) - 8} more" if len(names) > 8 else ""
            self._chat.append_html(
                f'<div class="tools">used {len(calls)} tool call(s): '
                f"{markdown.render(shown + extra)}</div>"
            )
        if result.get("tools_unavailable"):
            vision = find_vision_model()
            if vision is not None and vision_supports_tools(vision["name"]):
                self._chat.append_html(
                    '<div class="tools">This model can\'t use tools, so I answered '
                    f"without them (images still work). For full agentic use, "
                    f"pick <code>{vision['name']}</code> from the model menu.</div>"
                )
            else:
                # No tool-capable vision model installed: offer the download.
                model = self._config.get("vision_model") or DEFAULT_VISION_MODEL
                self._chat.append_html(
                    self._vision_install_card_html(
                        model,
                        "This model can't use tools, so I answered without "
                        "them. Install a vision model that can — one click:",
                    )
                )
        self._set_busy(False, f"Ready — {self._session.loaded.name}")

    # -- attachments --------------------------------------------------------
    def _attach_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach images",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        for file in files:
            self._add_pending_image(Path(file))

    def _add_pending_image(self, path: Path) -> None:
        if not image_helpers.is_image_file(path):
            return
        if path in self._pending_images or len(self._pending_images) >= 4:
            return
        self._pending_images.append(path)
        self._render_strip()

    def _clear_pending_images(self) -> None:
        self._pending_images = []
        self._render_strip()

    def _render_strip(self) -> None:
        layout = self._strip.layout()
        while layout.count() > 1:  # keep the trailing stretch
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for path in self._pending_images:
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            thumb = QLabel()
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                thumb.setPixmap(
                    pixmap.scaled(
                        72,
                        72,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                thumb.setText(path.name[:12])
            remove = QToolButton()
            remove.setText("×")
            remove.setToolTip(f"Remove {path.name}")
            remove.clicked.connect(
                lambda _checked=False, p=path: self._remove_pending_image(p)
            )
            cell_layout.addWidget(thumb, alignment=Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(remove, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.insertWidget(layout.count() - 1, cell)
        self._strip.setVisible(bool(self._pending_images))

    def _remove_pending_image(self, path: Path) -> None:
        if path in self._pending_images:
            self._pending_images.remove(path)
        self._render_strip()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            image_helpers.is_image_file(url.toLocalFile())
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and image_helpers.is_image_file(local):
                self._add_pending_image(Path(local))
        event.acceptProposedAction()

    # -- one-click vision install -------------------------------------------
    def _on_anchor(self, url: QUrl) -> None:
        if url.scheme() != "ttacode":
            return
        action = url.host()
        if action == "retry-setup":
            self._chat.append_html('<div class="tools">retrying setup…</div>')
            self._start_setup()
        elif action == "install-vision":
            self._start_pull()
        elif action == "install-model":
            name = url.path().lstrip("/")
            if name:
                self._start_pull(name)

    def _start_pull(self, model: str | None = None) -> None:
        if self._pulling:
            return
        model = model or self._config.get("vision_model") or DEFAULT_VISION_MODEL
        self._pull_model = model
        self._pulling = True
        self._set_busy(True, f"Installing {model}…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._pull_worker = auto_cleanup(PullWorker(model, parent=self))
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.done.connect(self._on_pull_done)
        self._pull_worker.start()

    def _on_pull_progress(self, percent: int, line: str) -> None:
        if percent >= 0:
            self._progress.setValue(percent)
        self._status_label.setText(f"Installing vision model… {line[:80]}")

    def _on_pull_done(self, ok: bool, message: str) -> None:
        self._pulling = False
        self._progress.setVisible(False)
        if not ok:
            self._set_busy(False, "Install failed")
            self._chat.append_html(
                '<div class="msg error"><div class="who">TTACODE</div>'
                f"<div>{markdown.render(message)}</div></div>"
            )
            return
        self._chat.append_html(
            f'<div class="tools">{markdown.render(message)} '
            "Switching to it…</div>"
        )
        self._config["default_model"] = self._pull_model or self._config.get(
            "vision_model"
        ) or DEFAULT_VISION_MODEL
        save_config(self._config)
        # Refresh the model list (the new model now exists), then switch.
        self._start_setup(model=self._config["default_model"])

    # -- project / new chat ---------------------------------------------------
    def _pick_folder(self) -> None:
        current = (
            str(self._session.project_path)
            if self._session
            else str(Path.home())
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose working folder", current
        )
        if not chosen:
            return
        self._config["project_root"] = chosen
        save_config(self._config)
        model = self._session.loaded.name if self._session else None
        self._chat.append_html(
            f'<div class="tools">working folder → '
            f"{markdown.render(chosen)}</div>"
        )
        self._start_setup(model=model, project=chosen)

    def _new_chat(self) -> None:
        if self._session is None or self._busy:
            return
        self._session.loop.reset()
        self._chat.clear()
        self._welcome()

    def closeEvent(self, event) -> None:
        if self._session is not None:
            from harness.session import close_session

            close_session(self._session)
        super().closeEvent(event)


class _ModelCombo(QComboBox):
    """Combo box with a minimum width so model names stay readable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setToolTip("Switch the local model")
