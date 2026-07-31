"""Unit tests for hybrid_retrieval — FTS5 + FAISS fusion."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from memory.hybrid_retrieval import hybrid_search, _l2_normalize


class TestL2Normalize:
    def test_unit_vector(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_almost_equal(result, v)

    def test_scaled_vector(self):
        v = np.array([3.0, 4.0], dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_almost_equal(result, np.array([0.6, 0.8], dtype=np.float32))


class TestHybridSearch:
    def test_fts5_only_when_vector_empty(self):
        emb = MagicMock()
        vs = MagicMock()
        vs.size.return_value = 0
        ltm = MagicMock()
        ltm.search.return_value = [
            {"id": "m1", "content": "test result"},
        ]
        results = hybrid_search("test query", embedding_service=emb,
                                vector_store=vs, ltm_store=ltm)
        assert len(results) == 1
        assert results[0]["id"] == "m1"

    def test_hybrid_combines_scores(self):
        emb = MagicMock()
        emb.encode_single.return_value = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        vs = MagicMock()
        vs.size.return_value = 2
        vs.search.return_value = [("m1", 0.9), ("m2", 0.5)]
        ltm = MagicMock()
        ltm.search.return_value = [
            {"id": "m1", "content": "fts match 1"},
            {"id": "m3", "content": "fts match 2"},
        ]
        results = hybrid_search("test", embedding_service=emb,
                                vector_store=vs, ltm_store=ltm, alpha=0.3)
        # m1 in both, m2 vector-only, m3 fts-only
        ids = {r["id"] for r in results}
        assert "m1" in ids

    def test_empty_results(self):
        emb = MagicMock()
        vs = MagicMock()
        vs.size.return_value = 0
        ltm = MagicMock()
        ltm.search.return_value = []
        results = hybrid_search("nothing", embedding_service=emb,
                                vector_store=vs, ltm_store=ltm)
        assert results == []
