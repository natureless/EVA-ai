"""Unit tests for VectorStore — add, search, size, dim."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from memory.vector_store import VectorStore


class TestVectorStore:
    def test_dim_default(self):
        vs = VectorStore()
        assert vs._dim == 384

    def test_size_initially_zero(self):
        vs = VectorStore()
        assert vs.size() == 0

    def test_add_and_size(self):
        vs = VectorStore(dim=4)
        # Inject a mock FAISS index
        mock_index = MagicMock()
        vs._index = mock_index
        vs.add("id1", np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
        assert vs.size() == 1

    def test_add_batch(self):
        vs = VectorStore(dim=4)
        mock_index = MagicMock()
        vs._index = mock_index
        items = [
            ("a", np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)),
            ("b", np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)),
        ]
        vs.add_batch(items)
        assert vs.size() == 2

    def test_search_returns_results(self):
        vs = VectorStore(dim=4)
        mock_index = MagicMock()
        mock_index.search.return_value = (np.array([[0.9, 0.5]]), np.array([[0, 1]]))
        vs._index = mock_index
        vs._id_to_idx = {"a": 0, "b": 1}
        vs._idx_to_id = {0: "a", 1: "b"}
        vs._next_idx = 2
        results = vs.search(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), k=2)
        assert len(results) == 2
        assert results[0][0] == "a"

    def test_search_empty_store(self):
        vs = VectorStore(dim=4)
        results = vs.search(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), k=5)
        assert results == []
