"""Entry point: ``python -m harness`` delegates to :mod:`harness.cli`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
