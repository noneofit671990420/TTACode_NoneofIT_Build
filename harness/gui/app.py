"""Entry point for the TTACode desktop GUI (``ttacode.exe``).

Used two ways:

* ``python -m harness gui`` (development)
* PyInstaller ``ttacode-gui.spec`` bundles this file as a windowed app.

``--version`` prints the version and exits *before* Qt is imported, so
the build smoke test stays fast and console-free binaries still report.

Launch order is guided and simple: the precheck dialog runs first
(Ollama → models → machine specs, every step announced, one-click fixes
for anything missing). Only when it accepts do we open the chat window —
the user can never land in an empty, unexplained window.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        from harness import __version__

        print(f"ttacode gui {__version__}")
        return 0

    from PySide6.QtWidgets import QApplication, QDialog

    from harness.gui.main_window import MainWindow
    from harness.gui.precheck import PrecheckDialog, apply_theme
    from harness.gui.theme import QSS

    app = QApplication(args)
    app.setApplicationName("TTACode")
    app.setOrganizationName("TTACode")
    app.setStyleSheet(QSS)

    precheck = PrecheckDialog()
    apply_theme(precheck)
    if precheck.exec() != QDialog.DialogCode.Accepted:
        return 0

    window = MainWindow(model=precheck.selected_model)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
