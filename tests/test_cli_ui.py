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

from harness.cli import build_parser, cmd_chat, cmd_gui, cmd_ui


class ChatCommandTests(unittest.TestCase):
    def test_parser_wires_chat_subcommand(self):
        args = build_parser().parse_args(["chat"])
        self.assertIs(args.func, cmd_chat)

    def test_chat_defaults(self):
        args = build_parser().parse_args(["chat", "--model", "x:1b"])
        self.assertEqual(args.model, "x:1b")
        self.assertIsNone(args.project)
        self.assertFalse(args.no_warm)

    def test_run_without_headless_points_at_chat(self):
        from harness.cli import cmd_run
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = cmd_run(Namespace(headless=False))
        self.assertEqual(rc, 2)
        self.assertIn("ttacode chat", err.getvalue())


    def test_parser_wires_gui_subcommand(self):
        args = build_parser().parse_args(["gui"])
        self.assertIs(args.func, cmd_gui)

    def test_frozen_gui_points_at_ttacode_exe(self):
        with patch.object(sys, "frozen", True, create=True):
            err = io.StringIO()
            with patch("sys.stderr", err):
                rc = cmd_gui(Namespace())
        self.assertEqual(rc, 2)
        self.assertIn("ttacode.exe", err.getvalue())
        self.assertNotIn("ttacode-studio.exe", err.getvalue())


class UiCommandTests(unittest.TestCase):
    def test_parser_wires_ui_subcommand(self):
        args = build_parser().parse_args(["ui"])
        self.assertIs(args.func, cmd_ui)

    def test_frozen_points_at_ttacode_exe(self):
        with patch.object(sys, "frozen", True, create=True):
            err = io.StringIO()
            with patch("sys.stderr", err):
                rc = cmd_ui(Namespace())
        self.assertEqual(rc, 2)
        self.assertIn("ttacode.exe", err.getvalue())
        self.assertNotIn("ttacode-studio.exe", err.getvalue())

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
