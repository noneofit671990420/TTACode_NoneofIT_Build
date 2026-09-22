"""Tests for the `harness ui` command. No Qt needed.

Covers: subcommand wiring, the frozen-binary message (points at
ttacode-studio.exe, exit 2), and the missing-Qt guidance (mentions
PySide6). The success path (actually launching Studio) needs a display
and Qt, so it is not exercised here.
"""

from __future__ import annotations

import io
import sys
import unittest
from argparse import Namespace
from unittest.mock import patch

from harness.cli import build_parser, cmd_ui


class UiCommandTests(unittest.TestCase):
    def test_parser_wires_ui_subcommand(self):
        args = build_parser().parse_args(["ui"])
        self.assertIs(args.func, cmd_ui)

    def test_frozen_points_at_studio_exe(self):
        with patch.object(sys, "frozen", True, create=True):
            err = io.StringIO()
            with patch("sys.stderr", err):
                rc = cmd_ui(Namespace())
        self.assertEqual(rc, 2)
        self.assertIn("ttacode-studio.exe", err.getvalue())

    def test_missing_qt_mentions_pyside6(self):
        sys.modules["studio"] = None  # makes `import studio` raise ImportError
        self.addCleanup(sys.modules.pop, "studio", None)
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = cmd_ui(Namespace())
        self.assertNotEqual(rc, 0)
        self.assertIn("PySide6", err.getvalue())


if __name__ == "__main__":
    unittest.main()
