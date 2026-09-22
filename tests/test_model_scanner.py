"""Unit tests for harness.models.scanner — stdlib unittest only."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from harness.models import scanner
from harness.models.scanner import (
    discover_all,
    discover_disk_models,
    discover_live_models,
    pick_default,
)


def _write_ollama_model(store: Path, name: str, tag: str, blob_sizes: list[int]):
    """Create a fake Ollama manifests+blobs layout for one model."""
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / name
    manifest_dir.mkdir(parents=True, exist_ok=True)
    blob_dir = store / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    layers = []
    for i, size in enumerate(blob_sizes):
        digest = f"sha256:blob{i:040d}"
        (blob_dir / digest.replace(":", "-")).write_bytes(b"x" * size)
        layers.append({"digest": digest, "size": size})
    manifest = {"layers": layers, "config": {"digest": "sha256:config", "size": 100}}
    (manifest_dir / tag).write_text(json.dumps(manifest), encoding="utf-8")


class TestOllamaDiskScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "ollama-models"

    def tearDown(self):
        self.tmp.cleanup()

    def test_tagged_model_found_with_blob_sizes(self):
        _write_ollama_model(self.store, "testmodel", "7b", [1000, 2000])
        models = discover_disk_models(ollama_store=self.store, lmstudio_store=Path("/nonexistent"))
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "testmodel:7b")
        self.assertEqual(models[0]["size_bytes"], 3000)
        self.assertEqual(models[0]["source"], "disk")

    def test_latest_tag_omits_suffix(self):
        _write_ollama_model(self.store, "plain", "latest", [500])
        models = discover_disk_models(ollama_store=self.store, lmstudio_store=Path("/nonexistent"))
        self.assertEqual(models[0]["name"], "plain")

    def test_malformed_manifest_is_skipped(self):
        manifest_dir = self.store / "manifests" / "registry.ollama.ai" / "library" / "broken"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "1b").write_text("{not valid json", encoding="utf-8")
        models = discover_disk_models(ollama_store=self.store, lmstudio_store=Path("/nonexistent"))
        self.assertEqual(models, [])

    def test_missing_store_returns_empty(self):
        models = discover_disk_models(
            ollama_store=Path("/definitely/not/here"),
            lmstudio_store=Path("/also/not/here"),
        )
        self.assertEqual(models, [])


class TestLMStudioDiskScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "lmstudio"

    def tearDown(self):
        self.tmp.cleanup()

    def test_gguf_files_found_recursively(self):
        target = self.store / "author" / "model-q4_k_m.gguf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"y" * 4096)
        (self.store / "notes.txt").write_text("not a model")
        models = discover_disk_models(
            ollama_store=Path("/nonexistent"), lmstudio_store=self.store
        )
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "author/model-q4_k_m")
        self.assertEqual(models[0]["size_bytes"], 4096)

    def test_missing_store_returns_empty(self):
        models = discover_disk_models(
            ollama_store=Path("/nonexistent"),
            lmstudio_store=Path("/definitely/not/here"),
        )
        self.assertEqual(models, [])


class TestLiveDiscovery(unittest.TestCase):
    def test_unreachable_ports_yield_empty(self):
        # Ports 1-2 are (almost certainly) closed; must not raise.
        result = discover_live_models(ports=(1, 2))
        self.assertEqual(result, {1: [], 2: []})


class TestDiscoverAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ollama = Path(self.tmp.name) / "ollama"
        _write_ollama_model(self.ollama, "diskmodel", "4b", [100])

    def tearDown(self):
        self.tmp.cleanup()
        # Restore in case a test patched the module function.
        import importlib

        importlib.reload(scanner)

    def test_merge_marks_both(self):
        original = scanner._live_tags
        scanner._live_tags = lambda port, timeout=3.0: ["diskmodel:4b", "liveonly:1b"]
        try:
            merged = discover_all(
                ports=(11434,),
                ollama_store=self.ollama,
                lmstudio_store=Path("/nonexistent"),
            )
        finally:
            scanner._live_tags = original
        by_name = {m["name"]: m for m in merged}
        self.assertEqual(by_name["diskmodel:4b"]["source"], "both")
        self.assertEqual(by_name["liveonly:1b"]["source"], "live")
        self.assertIsNone(by_name["liveonly:1b"]["size_bytes"])


class TestPickDefault(unittest.TestCase):
    def _rec(self, name, source="disk", size=1000):
        return {"name": name, "source": source, "size_bytes": size, "path": None}

    def test_none_when_nothing_downloaded(self):
        self.assertIsNone(pick_default([]))
        self.assertIsNone(pick_default([self._rec("x:1b", source="live")]))

    def test_prefers_instruct_over_plain(self):
        models = [self._rec("plain-8b"), self._rec("qwen3-instruct-4b")]
        self.assertEqual(pick_default(models)["name"], "qwen3-instruct-4b")

    def test_smallest_wins_ties(self):
        models = [self._rec("b-instruct", size=9000), self._rec("a-instruct", size=1000)]
        self.assertEqual(pick_default(models)["name"], "a-instruct")

    def test_avoids_huge_models_as_default(self):
        models = [self._rec("qwen3-30b-instruct", size=20_000), self._rec("phi-mini-instruct", size=2500)]
        self.assertEqual(pick_default(models)["name"], "phi-mini-instruct")

    def test_embedding_models_not_picked(self):
        models = [self._rec("nomic-embed-text"), self._rec("qwen3-4b-instruct")]
        self.assertEqual(pick_default(models)["name"], "qwen3-4b-instruct")

    def test_live_only_models_ignored(self):
        models = [self._rec("fancy:70b", source="live"), self._rec("tiny:1b", source="disk")]
        self.assertEqual(pick_default(models)["name"], "tiny:1b")


if __name__ == "__main__":
    unittest.main()
