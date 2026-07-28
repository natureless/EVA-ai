"""Tests for memory.hybrid_retrieval — score combination, fallback, alpha blending."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from memory.hybrid_retrieval import _l2_normalize, hybrid_search


class TestL2Normalize:
    def test_unit_vector_unchanged(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_almost_equal(result, v)

    def test_normalizes_to_unit_length(self):
        v = np.array([3.0, 4.0], dtype=np.float32)  # length 5
        result = _l2_normalize(v)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-6

    def test_zero_vector_unchanged(self):
        v = np.zeros(5, dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_equal(result, v)


class TestHybridSearch:
    DIM = 384

    @pytest.fixture
    def emb_svc(self):
        svc = MagicMock()
        svc.encode_single.return_value = np.ones(self.DIM, dtype=np.float32) / np.sqrt(self.DIM)
        return svc

    @pytest.fixture
    def vec_store(self):
        vs = MagicMock()
        vs.size.return_value = 0
        vs.search.return_value = []
        return vs

    @pytest.fixture
    def ltm_store(self):
        store = MagicMock()
        store.search.return_value = []
        store.get.return_value = None
        return store

    def _make_fts_results(self, store, rows):
        """Configure ltm_store.search to return given rows."""
        store.search.return_value = rows

    def _make_vec_results(self, vec_store, results):
        """Configure vec_store to return given (id, score) tuples."""
        vec_store.size.return_value = len(results) if results else 1
        vec_store.search.return_value = results

    def test_pure_fts5_when_vector_empty(self, emb_svc, vec_store, ltm_store):
        """When vector_store is empty, fall back to FTS5-only."""
        rows = [
            {"id": "a", "content": "first"},
            {"id": "b", "content": "second"},
        ]
        self._make_fts_results(ltm_store, rows)
        vec_store.size.return_value = 0

        results = hybrid_search(
            "test query",
            embedding_service=emb_svc,
            vector_store=vec_store,
            ltm_store=ltm_store,
            alpha=0.3,
            top_k=50,
        )
        assert len(results) == 2
        # embedding_service.encode_single should NOT be called
        emb_svc.encode_single.assert_not_called()

    def test_pure_vector_when_fts5_empty(self, emb_svc, vec_store, ltm_store):
        """When FTS5 returns nothing but vector has results, return vector results."""
        ltm_store.search.return_value = []
        ltm_store.get.return_value = {"id": "vec-a", "content": "semantic match"}

        self._make_vec_results(vec_store, [("vec-a", 0.95)])

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=0.3, top_k=50,
        )
        assert len(results) == 1
        assert results[0]["id"] == "vec-a"

    def test_hybrid_combines_both(self, emb_svc, vec_store, ltm_store):
        """When both FTS5 and vector return results, combine with alpha weight."""
        self._make_fts_results(ltm_store, [
            {"id": "fts-only", "content": "keyword match"},
        ])
        ltm_store.get.return_value = {"id": "vec-only", "content": "semantic match"}

        self._make_vec_results(vec_store, [("vec-only", 0.8)])

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=0.5, top_k=50,
        )
        ids = {r["id"] for r in results}
        assert "fts-only" in ids
        assert "vec-only" in ids

    def test_overlapping_ids_use_blended_score(self, emb_svc, vec_store, ltm_store):
        """When an item appears in both FTS5 and vector, scores are blended."""
        shared = {"id": "shared-1", "content": "in both"}
        self._make_fts_results(ltm_store, [shared])
        self._make_vec_results(vec_store, [("shared-1", 0.9)])

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=0.3, top_k=50,
        )
        assert len(results) == 1
        assert results[0]["id"] == "shared-1"

    def test_alpha_one_pure_bm25(self, emb_svc, vec_store, ltm_store):
        """alpha=1.0 should rank purely by FTS5."""
        rows = [
            {"id": "first", "content": "a"},
            {"id": "second", "content": "b"},
            {"id": "third", "content": "c"},
        ]
        self._make_fts_results(ltm_store, rows)
        self._make_vec_results(vec_store, [("second", 1.0)])  # vector prefers "second"

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=1.0, top_k=50,
        )
        # FTS5 order is preserved
        assert results[0]["id"] == "first"

    def test_alpha_zero_pure_vector(self, emb_svc, vec_store, ltm_store):
        """alpha=0.0 should rank purely by vector similarity."""
        self._make_fts_results(ltm_store, [
            {"id": "fts-first", "content": "keyword top"},
        ])
        self._make_vec_results(vec_store, [
            ("vec-first", 0.95),
            ("vec-second", 0.80),
        ])
        ltm_store.get.side_effect = lambda mid: {"id": mid, "content": mid}

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=0.0, top_k=50,
        )
        assert results[0]["id"] == "vec-first"
        assert results[1]["id"] == "vec-second"

    def test_vector_error_falls_back_to_fts5(self, emb_svc, vec_store, ltm_store):
        """When vector search raises, results come from FTS5 only."""
        rows = [{"id": "safe", "content": "fallback"}]
        self._make_fts_results(ltm_store, rows)
        vec_store.size.return_value = 1
        emb_svc.encode_single.side_effect = RuntimeError("model crash")

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=0.3, top_k=50,
        )
        assert len(results) == 1
        assert results[0]["id"] == "safe"

    def test_top_k_truncation(self, emb_svc, vec_store, ltm_store):
        """Results should be truncated to top_k."""
        rows = [{"id": f"r{i}", "content": str(i)} for i in range(20)]
        self._make_fts_results(ltm_store, rows)
        vec_store.size.return_value = 0

        results = hybrid_search(
            "test", embedding_service=emb_svc, vector_store=vec_store,
            ltm_store=ltm_store, alpha=1.0, top_k=5,
        )
        assert len(results) == 5
