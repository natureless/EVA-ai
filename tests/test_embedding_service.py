"""Unit tests for EmbeddingService — encode, dim, singleton."""

from unittest.mock import MagicMock

import numpy as np

from memory.embedding_service import EmbeddingService


class TestEmbeddingService:
    def test_dim_default(self):
        svc = EmbeddingService()
        assert svc.dim == 384

    def test_dim_custom(self):
        svc = EmbeddingService(model_name="custom-model")
        svc._dim = 768
        assert svc.dim == 768

    def test_encode_empty_returns_empty(self):
        svc = EmbeddingService()
        svc._model = MagicMock()
        result = svc.encode([])
        assert result.shape == (0, 384)

    def test_encode_delegates_to_model(self):
        svc = EmbeddingService()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        svc._model = mock_model
        svc._dim = 3
        result = svc.encode(["hello"])
        assert result.shape == (1, 3)
        mock_model.encode.assert_called_once()

    def test_encode_single(self):
        svc = EmbeddingService()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2]], dtype=np.float32)
        svc._model = mock_model
        svc._dim = 2
        result = svc.encode_single("hello")
        assert result.shape == (2,)
        assert result.dtype == np.float32

    def test_singleton_returns_same_instance(self):
        EmbeddingService._instance = None
        svc1 = EmbeddingService.singleton()
        svc2 = EmbeddingService.singleton()
        assert svc1 is svc2
        EmbeddingService._instance = None

    def test_model_lazy_loads(self):
        svc = EmbeddingService()
        # Model is initially None (lazy)
        assert svc._model is None
        # hasattr would trigger property, check via class dict
        assert "model" in type(svc).__dict__
