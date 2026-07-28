"""Tests for memory.embedding_service — lazy-loading, encode, singleton."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from memory.embedding_service import EmbeddingService


class TestEmbeddingService:
    DIM = 384

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        EmbeddingService._instance = None
        yield
        EmbeddingService._instance = None

    def _make_service(self):
        svc = EmbeddingService(model_name="test-model")
        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((3, self.DIM), dtype=np.float32)
        svc._model = mock_model
        return svc

    def test_dim_property(self):
        svc = EmbeddingService()
        assert svc.dim == self.DIM

    def test_encode_returns_correct_shape(self):
        svc = self._make_service()
        result = svc.encode(["hello", "world", "test"])
        assert result.shape == (3, self.DIM)
        assert result.dtype == np.float32

    def test_encode_empty_list(self):
        svc = self._make_service()
        result = svc.encode([])
        assert result.shape == (0, self.DIM)

    def test_encode_single_returns_1d(self):
        svc = self._make_service()
        result = svc.encode_single("hello")
        assert result.shape == (self.DIM,)
        svc._model.encode.assert_called_once()

    def test_singleton_returns_same_instance(self):
        a = EmbeddingService.singleton()
        b = EmbeddingService.singleton()
        assert a is b

    def test_singleton_respects_model_name(self):
        svc = EmbeddingService.singleton(model_name="custom-model")
        assert svc._model_name == "custom-model"

    def test_model_is_lazy_loaded(self):
        """Model is not loaded at init time — only on first access."""
        svc = EmbeddingService()
        assert svc._model is None
        # The model property triggers import, but since sentence-transformers
        # isn't installed, we verify the guard: accessing .model raises
        # ModuleNotFoundError, proving it was deferred (not loaded at init).
        with pytest.raises(ModuleNotFoundError):
            _ = svc.model
