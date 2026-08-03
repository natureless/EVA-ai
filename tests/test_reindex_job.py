"""Unit tests for reindex_job — FAISS index rebuild."""

from unittest.mock import MagicMock

import numpy as np

from memory.reindex_job import reindex_all, _l2_normalize


class TestL2Normalize:
    def test_unit_vector_unchanged(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_almost_equal(result, v)

    def test_scaled_vector_normalized(self):
        v = np.array([3.0, 4.0], dtype=np.float32)  # norm = 5
        result = _l2_normalize(v)
        np.testing.assert_array_almost_equal(result, np.array([0.6, 0.8], dtype=np.float32))

    def test_near_zero_vector_unchanged(self):
        v = np.array([0.0, 0.0], dtype=np.float32)
        result = _l2_normalize(v)
        np.testing.assert_array_equal(result, v)


class TestReindexAll:
    def test_empty_ltm_returns_zero(self):
        emb = MagicMock()
        vs = MagicMock()
        ltm = MagicMock()
        ltm.store.fetchall.return_value = []

        result = reindex_all(emb, vs, ltm)
        assert result["indexed"] == 0
        assert result["duration_sec"] == 0.0

    def test_reindex_with_rows(self):
        emb = MagicMock()
        emb.dim = 128
        emb.encode.return_value = np.random.randn(3, 128).astype(np.float32)

        vs = MagicMock()
        vs._index = None
        vs._id_to_idx = {}
        vs._idx_to_id = []
        vs._next_idx = 0

        ltm = MagicMock()
        ltm.store.fetchall.return_value = [
            {"id": "a", "content": "memory one"},
            {"id": "b", "content": "memory two"},
            {"id": "c", "content": "memory three"},
        ]

        result = reindex_all(emb, vs, ltm, batch_size=2)
        assert result["indexed"] == 3
        assert result["duration_sec"] >= 0
        emb.encode.assert_called()

    def test_reindex_handles_encode_error(self):
        emb = MagicMock()
        emb.dim = 128
        emb.encode.side_effect = RuntimeError("encode failed")

        vs = MagicMock()
        vs._index = None
        vs._id_to_idx = {}
        vs._idx_to_id = []
        vs._next_idx = 0
        vs.add_batch = MagicMock()

        ltm = MagicMock()
        ltm.store.fetchall.return_value = [
            {"id": "a", "content": "memory one"},
        ]

        result = reindex_all(emb, vs, ltm)
        assert result["indexed"] == 0
        assert result["skipped"] == 1

    def test_reindex_swaps_index_atomically(self):
        emb = MagicMock()
        emb.dim = 128
        emb.encode.return_value = np.random.randn(2, 128).astype(np.float32)

        vs = MagicMock()
        vs._index = "old_index"
        vs._id_to_idx = {}
        vs._idx_to_id = []
        vs._next_idx = 0
        vs.add_batch = MagicMock()

        ltm = MagicMock()
        ltm.store.fetchall.return_value = [
            {"id": "a", "content": "memory one"},
            {"id": "b", "content": "memory two"},
        ]

        reindex_all(emb, vs, ltm)
        # After reindex, vs._index should have been replaced
        assert vs._index != "old_index"
