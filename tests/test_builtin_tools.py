"""Unit tests for harness.tools.builtin — stdlib unittest only.

Covers path containment, checkpoint + restore roundtrips, bounded
grep, and the shell tool (using ``sys.executable -c`` one-liners so the
tests stay portable across Windows and POSIX).
"""

import sys
import tempfile
import unittest
from pathlib import Path

from harness.tools import ToolContext, ToolRegistry
from harness.tools.builtin import register_all


def _registry(tmpdir, config=None):
    ctx = ToolContext(project_root=tmpdir, config=config or {})
    registry = ToolRegistry()
    register_all(registry, ctx)
    return registry, ctx


class TestPathContainment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_escape_refused(self):
        registry, ctx = _registry(self.tmp.name)
        for evil in ("../evil.txt", "../../etc/passwd", "/etc/passwd",
                     str(Path(self.tmp.name).parent / "sibling.txt")):
            out = registry.call("read_file", {"path": evil})
            self.assertFalse(out["ok"], evil)
            self.assertIn("escapes", out["error"])

    def test_secret_files_refused(self):
        registry, ctx = _registry(self.tmp.name)
        for secret in (".env", "id_rsa", "tokens.json", "key.pem"):
            out = registry.call("write_file", {"path": secret, "content": "x"})
            self.assertFalse(out["ok"], secret)

    def test_git_metadata_refused(self):
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("write_file", {"path": ".git/config", "content": "x"})
        self.assertFalse(out["ok"])

    def test_delete_root_refused(self):
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("delete_path", {"path": "."})
        self.assertFalse(out["ok"])
        self.assertIn("project root", out["error"])

    def test_direct_resolve_raises(self):
        _, ctx = _registry(self.tmp.name)
        with self.assertRaises(ValueError):
            ctx.resolve("../outside")


class TestCheckpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_then_restore_new_file(self):
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("write_file", {"path": "n.txt", "content": "v1"})
        self.assertTrue(out["ok"])
        self.assertTrue((self.root / "n.txt").exists())
        back = registry.call("restore_change", {"checkpoint_id": out["checkpoint"]})
        self.assertTrue(back["ok"])
        self.assertFalse((self.root / "n.txt").exists())

    def test_write_then_restore_existing_file(self):
        (self.root / "e.txt").write_text("original")
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("write_file", {"path": "e.txt", "content": "changed"})
        self.assertTrue(out["ok"])
        back = registry.call("restore_change", {"checkpoint_id": out["checkpoint"]})
        self.assertTrue(back["ok"])
        self.assertEqual((self.root / "e.txt").read_text(), "original")

    def test_restore_refuses_after_external_change(self):
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("write_file", {"path": "x.txt", "content": "v1"})
        (self.root / "x.txt").write_text("someone else edited")
        back = registry.call("restore_change", {"checkpoint_id": out["checkpoint"]})
        self.assertFalse(back["ok"])
        self.assertIn("changed since", back["error"])

    def test_restore_unknown_checkpoint(self):
        registry, ctx = _registry(self.tmp.name)
        back = registry.call("restore_change", {"checkpoint_id": "nope"})
        self.assertFalse(back["ok"])

    def test_edit_file_requires_unique_match(self):
        (self.root / "d.txt").write_text("aaa bbb aaa")
        registry, ctx = _registry(self.tmp.name)
        out = registry.call("edit_file", {"path": "d.txt", "old_text": "aaa",
                                           "new_text": "zzz"})
        self.assertFalse(out["ok"])
        self.assertIn("exactly once", out["error"])
        out = registry.call("edit_file", {"path": "d.txt", "old_text": "aaa bbb aaa",
                                           "new_text": "zzz"})
        self.assertTrue(out["ok"])
        self.assertEqual((self.root / "d.txt").read_text(), "zzz")

    def test_write_file_checked(self):
        registry, ctx = _registry(self.tmp.name)
        # __absent__ on missing file: allowed.
        out = registry.call("write_file_checked", {
            "path": "c.txt", "content": "one", "expected_sha256": "__absent__"})
        self.assertTrue(out["ok"])
        # __absent__ on existing file: refused.
        out = registry.call("write_file_checked", {
            "path": "c.txt", "content": "two", "expected_sha256": "__absent__"})
        self.assertFalse(out["ok"])
        # Correct sha: allowed.
        fp = registry.call("file_fingerprint", {"path": "c.txt"})
        out = registry.call("write_file_checked", {
            "path": "c.txt", "content": "two",
            "expected_sha256": fp["sha256"]})
        self.assertTrue(out["ok"])
        self.assertEqual((self.root / "c.txt").read_text(), "two")
        # Wrong sha: refused.
        out = registry.call("write_file_checked", {
            "path": "c.txt", "content": "three", "expected_sha256": "deadbeef"})
        self.assertFalse(out["ok"])

    def test_changes_recorded(self):
        registry, ctx = _registry(self.tmp.name)
        registry.call("write_file", {"path": "a.txt", "content": "1"})
        registry.call("write_file", {"path": "b.txt", "content": "2"})
        self.assertEqual(len(ctx.changes), 2)
        self.assertEqual(ctx.changes[0]["path"], "a.txt")

    def test_discard_checkpoint_cleans_up(self):
        _, ctx = _registry(self.tmp.name)
        target = ctx.resolve("w.txt")
        cid = ctx.snapshot(target, None, b"new-bytes")
        self.assertEqual(len(ctx.changes), 1)
        ctx.discard_checkpoint(cid)
        self.assertEqual(ctx.changes, [])
        with self.assertRaises(ValueError):
            ctx.restore(cid)

    def test_snapshot_before_write_discipline(self):
        # The checkpoint must exist with the ORIGINAL bytes even though
        # the write happens after snapshotting (snapshot-before-mutation).
        registry, ctx = _registry(self.tmp.name)
        (self.root / "pre.txt").write_text("before")
        out = registry.call("write_file", {"path": "pre.txt", "content": "after"})
        self.assertTrue(out["ok"])
        original = (ctx.checkpoint_base / out["checkpoint"] / "original")
        self.assertTrue(original.is_file())
        self.assertEqual(original.read_bytes(), b"before")


class TestGrepAndDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text("def foo():\n    pass\n# needle here\n")
        (self.root / "b.py").write_text("nothing\n")
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("needle again\nneedle twice\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_grep_finds_matches(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("grep_search", {"query": "needle"})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["hits"]), 3)
        self.assertTrue(all("needle" in h for h in out["hits"]))

    def test_grep_bounded(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("grep_search", {"query": "needle", "max_results": 2})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["hits"]), 2)
        self.assertTrue(out["truncated"])

    def test_grep_bad_regex(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("grep_search", {"query": "([unclosed"})
        self.assertFalse(out["ok"])

    def test_mkdir_list_delete(self):
        registry, _ = _registry(self.tmp.name)
        self.assertTrue(registry.call("mkdir", {"path": "newdir"})["ok"])
        out = registry.call("list_dir", {"path": "."})
        self.assertTrue(out["ok"])
        self.assertIn("newdir/", out["entries"])
        self.assertTrue(registry.call("delete_path", {"path": "newdir"})["ok"])
        out = registry.call("list_dir", {"path": "."})
        self.assertNotIn("newdir/", out["entries"])


class TestShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_echo_capture(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("run_command", {
            "command": f'"{sys.executable}" -c "print(\'hi from shell\')"',
            "timeout": 30,
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["returncode"], 0)
        self.assertIn("hi from shell", out["output"])
        self.assertIn("Exit 0", out["output"])

    def test_nonzero_exit(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("run_command", {
            "command": f'"{sys.executable}" -c "import sys; sys.exit(3)"',
            "timeout": 30,
        })
        self.assertTrue(out["ok"])  # the tool ran fine; the command failed
        self.assertEqual(out["returncode"], 3)
        self.assertIn("Exit 3", out["output"])

    def test_timeout_kills(self):
        registry, _ = _registry(self.tmp.name, config={"shell_timeout": 60})
        out = registry.call("run_command", {
            "command": f'"{sys.executable}" -c "import time; time.sleep(60)"',
            "timeout": 3,
        })
        self.assertFalse(out["ok"])
        self.assertIn("timed out", out["error"])

    def test_empty_command_refused(self):
        registry, _ = _registry(self.tmp.name)
        out = registry.call("run_command", {"command": "   "})
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
