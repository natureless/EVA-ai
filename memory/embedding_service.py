"""Lazy-loading singleton for sentence-transformers text embeddings."""

from __future__ import annotations

import logging
from typing import Any, Optional
import numpy as np

logger = logging.getLogger("eva.embedding")


class EmbeddingService:
    """Lazy-loads a sentence-transformers model for text embedding."""

    _instance: Optional[EmbeddingService] = None

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model = None
        self._dim = 384  # all-MiniLM-L6-v2 output dimension

    @property
    def model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("loading embedding model: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Return (n, dim) float32 array."""
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        return self.model.encode(  # type: ignore[no-any-return]
            texts, batch_size=batch_size, show_progress_bar=False,
        )

    def encode_single(self, text: str) -> np.ndarray:
        """Return (dim,) float32 array for a single text."""
        return self.encode([text])[0]  # type: ignore[no-any-return]

    @classmethod
    def singleton(cls, model_name: str = "all-MiniLM-L6-v2") -> EmbeddingService:
        if cls._instance is None:
            cls._instance = cls(model_name=model_name)
        return cls._instance
