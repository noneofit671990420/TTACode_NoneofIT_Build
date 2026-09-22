"""Unit tests for harness.models.loader — stdlib unittest only."""

import unittest
from unittest import mock

from harness.models import loader
from harness.models.loader import (
    ModelLoadError,
    detect_vram_gb,
    load_model,
    vram_budget_bytes,
)
from harness.models.scanner import pick_default

GIB = 1024**3


def _m(name, source="disk", size=None):
    return {"name": name, "source": source, "size_bytes": size, "path": None}


class FakeTransport:
    """Stand-in for OllamaTransport: records tuning, counts warm() calls."""

    def __init__(self, url, model, **kwargs):
        self.url = url
        self.model = model
        self.kwargs = kwargs
        self.warm_calls = 0
        self.warm_result = True

    def _options(self):
        opts = {"num_ctx": self.kwargs.get("num_ctx", 8192)}
        if self.kwargs.get("num_gpu"):
            opts["num_gpu"] = self.kwargs["num_gpu"]
        return opts

    def warm(self):
        self.warm_calls += 1
        return self.warm_result


class TestVramAwarePick(unittest.TestCase):
    def test_prefers_fitting_model_over_capable_but_oversize(self):
        models = [
            _m("qwen3:32b-instruct", size=int(19 * GIB)),
            _m("qwen2.5-coder:7b-instruct", size=int(4.7 * GIB)),
        ]
        winner = pick_default(models, vram_gb=8.0)
        self.assertEqual(winner["name"], "qwen2.5-coder:7b-instruct")

    def test_nothing_fits_falls_back_to_plain_ranking(self):
        # Nothing fits 8 GiB: fall back to the plain ranking
        # (capability first, then smallest) — never an exclusion.
        models = [
            _m("big-a:70b", size=int(40 * GIB)),
            _m("big-b:70b", size=int(70 * GIB)),
        ]
        winner = pick_default(models, vram_gb=8.0)
        self.assertEqual(winner["name"], "big-a:70b")

    def test_unknown_size_never_excluded(self):
        models = [_m("mystery:latest", size=None)]
        winner = pick_default(models, vram_gb=8.0)
        self.assertEqual(winner["name"], "mystery:latest")

    def test_no_vram_keeps_legacy_ranking(self):
        models = [
            _m("qwen3:32b-instruct", size=int(19 * GIB)),
            _m("tiny:1b", size=int(1 * GIB)),
        ]
        # No VRAM info: capability score wins over size.
        winner = pick_default(models)
        self.assertEqual(winner["name"], "qwen3:32b-instruct")

    def test_none_when_nothing_servable(self):
        self.assertIsNone(pick_default([], vram_gb=8.0))
        lm = _m("author/m-q4_k_m", size=int(4 * GIB))
        lm["store"] = "lmstudio"
        self.assertIsNone(pick_default([lm], vram_gb=8.0))

    def test_live_model_is_servable_pick(self):
        self.assertEqual(
            pick_default([_m("x", source="live")], vram_gb=8.0)["name"], "x"
        )

    def test_budget_is_90_percent(self):
        self.assertEqual(vram_budget_bytes(8.0), int(7.2 * GIB))
        self.assertIsNone(vram_budget_bytes(None))
        self.assertIsNone(vram_budget_bytes(0))


class TestDetectVram(unittest.TestCase):
    def test_missing_nvidia_smi_returns_none(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(detect_vram_gb())

    def test_parses_mib_to_gib(self):
        fake = mock.Mock(returncode=0, stdout="8192\n")
        with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(detect_vram_gb(), 8.0)

    def test_failed_command_returns_none(self):
        fake = mock.Mock(returncode=1, stdout="")
        with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertIsNone(detect_vram_gb())

    def test_exception_returns_none(self):
        with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             mock.patch("subprocess.run", side_effect=OSError("nope")):
            self.assertIsNone(detect_vram_gb())

    def test_garbage_output_returns_none(self):
        fake = mock.Mock(returncode=0, stdout="not-a-number\n")
        with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertIsNone(detect_vram_gb())


class TestLoadModel(unittest.TestCase):
    def setUp(self):
        self.models = [
            _m("qwen3:32b-instruct", size=int(19 * GIB)),
            _m("qwen2.5-coder:7b-instruct", size=int(4.7 * GIB)),
        ]
        self.transports = []

        def fake_transport(url, model, **kwargs):
            t = FakeTransport(url, model, **kwargs)
            self.transports.append(t)
            return t

        self._patches = [
            mock.patch.object(loader, "discover_all", return_value=self.models),
            mock.patch.object(
                loader, "resolve_model_url", return_value="http://127.0.0.1:11434"
            ),
            mock.patch.object(loader, "OllamaTransport", side_effect=fake_transport),
        ]
        for p in self._patches:
            p.start()
        self.quiet = lambda *a: None

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_auto_pick_warms_and_applies_tuning(self):
        loaded = load_model(
            None, {"vram_gb": 8.0}, out=self.quiet
        )
        # VRAM-aware pick: the 7b fits 8 GiB, the 32b does not.
        self.assertEqual(loaded.name, "qwen2.5-coder:7b-instruct")
        self.assertTrue(loaded.warmed)
        t = self.transports[0]
        self.assertEqual(t.warm_calls, 1)
        self.assertEqual(t.kwargs["num_ctx"], 8192)
        self.assertEqual(t.kwargs["keep_alive"], "30m")
        self.assertEqual(t.kwargs["num_gpu"], -1)  # full offload default
        self.assertIn("keep_alive", loaded.options)
        self.assertTrue(loaded.fits_vram)

    def test_oversize_pick_reports_no_fit(self):
        loaded = load_model(
            "qwen3:32b-instruct", {"vram_gb": 8.0}, out=self.quiet
        )
        self.assertEqual(loaded.name, "qwen3:32b-instruct")
        self.assertFalse(loaded.fits_vram)

    def test_explicit_unknown_model_raises_honestly(self):
        with self.assertRaises(ModelLoadError):
            load_model("does-not-exist:99b", {}, out=self.quiet)

    def test_explicit_lmstudio_disk_model_fails_fast_with_create_guidance(self):
        lm = _m("author/model-q4_k_m", size=int(4.7 * GIB))
        lm["store"] = "lmstudio"
        lm["path"] = "/models/model.gguf"
        with mock.patch.object(loader, "discover_all", return_value=self.models + [lm]):
            with self.assertRaises(ModelLoadError) as ctx:
                load_model("author/model-q4_k_m", {}, out=self.quiet)
        self.assertIn("ollama create", str(ctx.exception))

    def test_auto_pick_with_only_lmstudio_models_suggests_import(self):
        lm = _m("author/model-q4_k_m", size=int(4.7 * GIB))
        lm["store"] = "lmstudio"
        lm["path"] = "/models/model.gguf"
        with mock.patch.object(loader, "discover_all", return_value=[lm]):
            with self.assertRaises(ModelLoadError) as ctx:
                load_model(None, {"vram_gb": 8.0}, out=self.quiet)
        msg = str(ctx.exception)
        self.assertIn("ollama create", msg)
        self.assertIn("ollama pull", msg)

    def test_no_models_raises_honestly(self):
        with mock.patch.object(loader, "discover_all", return_value=[]):
            with self.assertRaises(ModelLoadError):
                load_model(None, {}, out=self.quiet)

    def test_unreachable_server_raises_honestly(self):
        with mock.patch.object(loader, "resolve_model_url", return_value=None):
            with self.assertRaises(ModelLoadError):
                load_model(None, {}, out=self.quiet)

    def test_no_warm_skips_warmup(self):
        loaded = load_model(None, {"vram_gb": 8.0}, warm=False, out=self.quiet)
        self.assertFalse(loaded.warmed)
        self.assertEqual(self.transports[0].warm_calls, 0)

    def test_warm_failure_is_warning_not_fatal(self):
        with mock.patch.object(
            loader, "OllamaTransport",
            side_effect=lambda url, model, **kw: _FailingWarm(url, model, **kw),
        ):
            said = []
            loaded = load_model(None, {"vram_gb": 8.0}, out=said.append)
            self.assertFalse(loaded.warmed)
            self.assertTrue(any("Warning" in s for s in said))

    def test_old_config_without_new_keys_still_loads(self):
        # Backward compat: a config from before vram_gb/warm_on_load existed.
        loaded = load_model(None, {"ollama_num_ctx": 4096}, out=self.quiet)
        t = self.transports[0]
        self.assertEqual(t.kwargs["num_ctx"], 4096)
        self.assertIsNone(loaded.vram_gb)
        self.assertIsNone(loaded.fits_vram)
        self.assertTrue(loaded.warmed)  # warm_on_load defaults True

    def test_config_values_pass_through(self):
        loaded = load_model(
            None,
            {
                "vram_gb": 8.0,
                "ollama_num_gpu": 999,
                "ollama_num_ctx": 4096,
                "ollama_keep_alive": "24h",
                "warm_on_load": False,
            },
            out=self.quiet,
        )
        t = self.transports[0]
        self.assertEqual(t.kwargs["num_gpu"], 999)
        self.assertEqual(t.kwargs["num_ctx"], 4096)
        self.assertEqual(t.kwargs["keep_alive"], "24h")
        self.assertFalse(loaded.warmed)

    def test_summary_line_mentions_model_and_warm_state(self):
        said = []
        load_model(None, {"vram_gb": 8.0}, out=said.append)
        joined = " ".join(said)
        self.assertIn("qwen2.5-coder:7b-instruct", joined)
        self.assertIn("warmed yes", joined)


class _FailingWarm(FakeTransport):
    def warm(self):
        self.warm_calls += 1
        return False


if __name__ == "__main__":
    unittest.main()
