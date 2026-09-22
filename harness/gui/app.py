"""Entry point for the TTACode desktop GUI (``ttacode.exe``).

Used two ways:

* ``python -m harness gui`` (development)
* PyInstaller ``ttacode-gui.spec`` bundles this file as a windowed app.

``--version`` prints the version and exits *before* Qt is imported, so
the build smoke test stays fast and console-free binaries still report.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        from harness import __version__

        print(f"ttacode gui {__version__}")
        return 0

    from PySide6.QtWidgets import QApplication

    from harness.gui.main_window import MainWindow
    from harness.gui.theme import QSS

    app = QApplication(args)
    app.setApplicationName("TTACode")
    app.setOrganizationName("TTACode")
    app.setStyleSheet(QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
