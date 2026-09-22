"""Unit tests for harness.tools.plugins — stdlib unittest only."""

import tempfile
import textwrap
import unittest
from pathlib import Path

from harness.tools import ToolContext, ToolRegistry, load_plugins
from harness.tools.schema import function_schema

_GOOD_PLUGIN = textwrap.dedent('''\
    PLUGIN_META = {"name": "demo-plugin", "version": "0.2.0",
                   "description": "Demo plugin for tests."}

    from harness.tools.schema import function_schema

    def _dummy(args):
        return {"ok": True, "echo": args.get("text", "")}

    def register_tools(registry, ctx):
        registry.register(
            "dummy_tool",
            "Echoes text back.",
            function_schema("dummy_tool", "Echoes text back.",
                            {"text": "Text to echo"}),
            _dummy,
        )
    ''')

_BROKEN_PLUGIN = "raise RuntimeError('boom at import time')\n"

_NO_REGISTER_PLUGIN = textwrap.dedent('''\
    PLUGIN_META = {"name": "lazy"}
    # note: no register_tools defined
    ''')

_RAISING_REGISTER_PLUGIN = textwrap.dedent('''\
    from harness.tools.schema import function_schema

    def _half(args):
        return {"ok": True}

    def register_tools(registry, ctx):
        # Registers one tool, THEN blows up: the loader must roll back
        # the partial registration.
        registry.register(
            "half_registered_tool",
            "Should not survive.",
            function_schema("half_registered_tool", "Should not survive.", {}),
            _half,
        )
        raise ValueError("registration exploded")
    ''')


class TestPluginLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.tmp.name) / "plugins"
        self.plugin_dir.mkdir()
        (self.plugin_dir / "good_plugin.py").write_text(_GOOD_PLUGIN)
        (self.plugin_dir / "broken_plugin.py").write_text(_BROKEN_PLUGIN)
        (self.plugin_dir / "no_register_plugin.py").write_text(_NO_REGISTER_PLUGIN)
        (self.plugin_dir / "raising_plugin.py").write_text(_RAISING_REGISTER_PLUGIN)
        self.ctx = ToolContext(project_root=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_plugin_registered(self):
        registry = ToolRegistry()
        result = load_plugins(registry, self.ctx, dirs=[self.plugin_dir])
        names = [m["name"] for m in result["loaded"]]
        self.assertIn("demo-plugin", names)
        meta = next(m for m in result["loaded"] if m["name"] == "demo-plugin")
        self.assertEqual(meta["version"], "0.2.0")
        self.assertEqual(meta["tools"], ["dummy_tool"])
        # The tool actually works through the registry.
        out = registry.call("dummy_tool", {"text": "hi"})
        self.assertEqual(out, {"ok": True, "echo": "hi"})

    def test_broken_plugins_do_not_crash_loading(self):
        registry = ToolRegistry()
        result = load_plugins(registry, self.ctx, dirs=[self.plugin_dir])
        failed_paths = [f["path"] for f in result["failed"]]
        self.assertEqual(len(result["failed"]), 3)  # broken, no_register, raising
        self.assertTrue(any("broken_plugin.py" in p for p in failed_paths))
        self.assertTrue(any("no_register_plugin.py" in p for p in failed_paths))
        self.assertTrue(any("raising_plugin.py" in p for p in failed_paths))
        # ...but the good one still loaded.
        self.assertIsNotNone(registry.get("dummy_tool"))

    def test_partial_registration_is_rolled_back(self):
        registry = ToolRegistry()
        load_plugins(registry, self.ctx, dirs=[self.plugin_dir])
        # raising_plugin registered half_registered_tool before raising;
        # the loader must have removed it again.
        self.assertIsNone(registry.get("half_registered_tool"))

    def test_missing_dir_is_fine(self):
        registry = ToolRegistry()
        result = load_plugins(registry, self.ctx,
                              dirs=[Path(self.tmp.name) / "does-not-exist"])
        self.assertEqual(result, {"loaded": [], "failed": []})

    def test_dunder_files_skipped(self):
        (self.plugin_dir / "__init__.py").write_text("")
        registry = ToolRegistry()
        result = load_plugins(registry, self.ctx, dirs=[self.plugin_dir])
        self.assertFalse(any("__init__" in f["path"] for f in result["failed"]))


if __name__ == "__main__":
    unittest.main()
