"""Tests for memory.vector_store — FAISS add, search, remove, save/load."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from memory.vector_store import VectorStore


DIM = 128  # smaller dim for faster tests


def _random_vec(dim=DIM) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)  # L2-normalized for IP


class TestVectorStore:
    @pytest.fixture
    def store(self):
        return VectorStore(dim=DIM)

    def test_initial_size_is_zero(self, store):
        assert store.size() == 0

    def test_add_and_search(self, store):
        v = _random_vec()
        store.add("mem-1", v)
        assert store.size() == 1

        results = store.search(v, k=10)
        assert len(results) == 1
        assert results[0][0] == "mem-1"
        assert results[0][1] > 0.9  # near-perfect cosine similarity

    def test_search_returns_highest_score_first(self, store):
        v1 = _random_vec()
        v2 = _random_vec()
        store.add("a", v1)
        store.add("b", v2)

        # Search with v1 — expect "a" to score highest
        results = store.search(v1, k=2)
        assert results[0][0] == "a"
        assert results[0][1] > results[1][1]

    def test_empty_store_search(self, store):
        results = store.search(_random_vec(), k=10)
        assert results == []

    def test_remove_soft_deletes(self, store):
        v = _random_vec()
        store.add("mem-x", v)
        store.remove("mem-x")
        # Size stays same (soft delete — index rebuild needed for true removal)
        assert store.size() == 1
        # But the ID mapping is gone
        results = store.search(v, k=1)
        assert len(results) == 0 or results[0][0] != "mem-x"

    def test_remove_nonexistent_does_not_crash(self, store):
        store.remove("does-not-exist")

    def test_batch_add(self, store):
        items = [(f"id-{i}", _random_vec()) for i in range(10)]
        store.add_batch(items)
        assert store.size() == 10

        # Search should find results
        results = store.search(items[0][1], k=5)
        assert len(results) == 5
        assert results[0][0] == "id-0"

    def test_batch_add_empty(self, store):
        store.add_batch([])
        assert store.size() == 0

    def test_save_load_roundtrip(self, store):
        v1 = _random_vec()
        v2 = _random_vec()
        store.add("alpha", v1)
        store.add("beta", v2)

        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = Path(tmpdir) / "test_index.faiss"
            store._index_path = idx_path
            store.save()

            # Load into a new store
            store2 = VectorStore(dim=DIM, index_path=idx_path)
            store2.load()

            assert store2.size() == 2
            results = store2.search(v1, k=2)
            ids = {r[0] for r in results}
            assert "alpha" in ids
            assert "beta" in ids

            # Check map file exists
            map_path = idx_path.with_suffix(".map.json")
            assert map_path.exists()
            map_data = json.loads(map_path.read_text())
            assert map_data["next_idx"] == 2

    def test_save_no_path_no_op(self, store):
        store.add("x", _random_vec())
        store.save()  # should not crash when _index_path is None

    def test_load_no_file_no_op(self, store):
        store._index_path = Path("/nonexistent/path.faiss")
        store.load()  # should not crash
        assert store.size() == 0

    def test_search_with_k_larger_than_store(self, store):
        store.add("only", _random_vec())
        results = store.search(_random_vec(), k=100)
        assert len(results) == 1
