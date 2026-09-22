"""Entry point: ``python -m harness`` delegates to :mod:`harness.cli`.

The import is absolute (not relative) on purpose: PyInstaller bundles
this file as a top-level script with no parent package, where relative
imports fail. Absolute import works identically under ``python -m``.
"""

from harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
