"""Packaging guard: the harness CLI must stay Qt-free and stdlib-only.

These tests run the CLI in a FRESH interpreter so a Qt import anywhere
in the harness import graph (or a new non-stdlib dependency) fails here
instead of surprising the PyInstaller build.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter with the repo on sys.path.

    Written to a temp file (rather than ``-c``) so multi-line probes
    don't fight shell quoting.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(code)
        script = handle.name
    try:
        return subprocess.run(
            [sys.executable, script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        Path(script).unlink(missing_ok=True)


_IMPORT_PROBE = (
    "import sys\n"
    "sys.path.insert(0, {root!r})\n"
    "import harness, harness.cli\n"
    "bad = [m for m in sys.modules\n"
    "       if m == 'PySide6' or m.startswith('PySide6.')\n"
    "       or m in ('PyQt5', 'PyQt6', 'tkinter')]\n"
    "print('BAD:' + ','.join(sorted(bad)) if bad else 'CLEAN')\n"
).format(root=str(REPO_ROOT))

_HELP_PROBE = (
    "import sys\n"
    "sys.path.insert(0, {root!r})\n"
    "from harness.cli import main\n"
    "bad_before = [m for m in sys.modules if 'PySide' in m or 'PyQt' in m]\n"
    "try:\n"
    "    main(['--help'])\n"
    "except SystemExit as e:\n"
    "    rc = e.code\n"
    "bad_after = [m for m in sys.modules if 'PySide' in m or 'PyQt' in m]\n"
    "print('RC:' + str(rc))\n"
    "bad = sorted(set(bad_before) | set(bad_after))\n"
    "print('BAD:' + ','.join(bad) if bad else 'CLEAN')\n"
).format(root=str(REPO_ROOT))


class TestPackagingGuards(unittest.TestCase):
    def test_cli_import_pulls_no_qt(self):
        proc = _run_isolated(_IMPORT_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("CLEAN", proc.stdout, proc.stdout)

    def test_help_runs_without_qt(self):
        proc = _run_isolated(_HELP_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("RC:0", proc.stdout, proc.stdout)
        self.assertIn("CLEAN", proc.stdout, proc.stdout)

    def test_main_module_has_no_heavy_imports(self):
        source = (REPO_ROOT / "harness" / "__main__.py").read_text(encoding="utf-8")
        for banned in ("PySide6", "PyQt5", "PyQt6", "tkinter"):
            self.assertNotIn(banned, source)

    def test_spec_file_exists_and_is_sane(self):
        spec = REPO_ROOT / "ttacode.spec"
        self.assertTrue(spec.is_file(), "ttacode.spec missing from repo root")
        text = spec.read_text(encoding="utf-8")
        self.assertIn("harness/__main__.py", text.replace("\\", "/"))
        self.assertIn("ttacode-cli", text)
        for banned in ("PySide6", "PyQt5", "PyQt6"):
            self.assertIn(banned, text, f"spec should exclude {banned}")

    def test_gui_spec_file_exists_and_is_sane(self):
        spec = REPO_ROOT / "ttacode-gui.spec"
        self.assertTrue(spec.is_file(), "ttacode-gui.spec missing from repo root")
        text = spec.read_text(encoding="utf-8")
        self.assertIn("harness/gui/app.py", text.replace("\\", "/"))
        self.assertIn('name="ttacode"', text)
        self.assertIn("console=False", text)
        self.assertIn("harness.gui.main_window", text)
        # The GUI build must bundle Qt — the excludes list must not
        # mention it (comments don't count).
        excludes_block = text[text.index("excludes=["): text.index("noarchive=False")]
        code_only = "\n".join(
            line.split("#", 1)[0] for line in excludes_block.splitlines()
        )
        for banned in ("PySide6", "PyQt5", "PyQt6"):
            self.assertNotIn(banned, code_only)


if __name__ == "__main__":
    unittest.main()
