"""Hybrid FTS5 + vector search with alpha-weighted score combination."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from memory.embedding_service import EmbeddingService
    from memory.vector_store import VectorStore
    from memory.tiered_store import LongTermMemoryStore

logger = logging.getLogger("eva.hybrid")


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return vec
    return vec / norm


def hybrid_search(
    query: str,
    *,
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
    ltm_store: LongTermMemoryStore,
    alpha: float = 0.3,
    top_k: int = 50,
) -> list[dict]:
    """Hybrid keyword (FTS5) + semantic (FAISS) search over long-term memory.

    alpha=1.0 → pure BM25 (keyword), alpha=0.0 → pure cosine similarity.
    Falls back to FTS5-only when vector_store is empty.
    """
    # 1. FTS5 keyword search (existing path)
    fts_results = ltm_store.search(query, limit=top_k * 2)
    fts_map: dict[str, tuple[dict, float]] = {}  # id → (row, raw_score)
    for i, row in enumerate(fts_results):
        score = 1.0 - (i / max(len(fts_results), 1))  # pseudo-BM25: rank-based
        fts_map[row["id"]] = (row, score)

    # 2. Vector search
    vec_results: list[tuple[str, float]] = []
    if vector_store.size() > 0 and embedding_service is not None:
        try:
            q_vec = embedding_service.encode_single(query)
            q_vec = _l2_normalize(q_vec)
            vec_results = vector_store.search(q_vec, k=top_k * 2)
        except Exception:
            logger.exception("vector search failed — falling back to FTS5")

    vec_map: dict[str, float] = {}
    max_vec = max((s for _, s in vec_results), default=1.0)
    for mid, score in vec_results:
        vec_map[mid] = score / max(max_vec, 0.001)

    # 3. Normalize FTS5 scores
    max_fts = max((s for s in fts_map.values()), default=1.0)
    for mid in fts_map:
        row, score = fts_map[mid]
        fts_map[mid] = (row, score / max(max_fts, 0.001))

    # 4. Combine scores
    all_ids = set(fts_map.keys()) | set(vec_map.keys())
    combined: list[tuple[dict, float]] = []
    for mid in all_ids:
        fts_score = fts_map[mid][1] if mid in fts_map else 0.0
        vec_score = vec_map.get(mid, 0.0)
        hybrid_score = alpha * fts_score + (1.0 - alpha) * vec_score
        row = fts_map[mid][0] if mid in fts_map else ltm_store.get(mid)
        if row:
            combined.append((row, hybrid_score))

    # 5. Sort by combined score, return top_k
    combined.sort(key=lambda x: x[1], reverse=True)
    return [row for row, _ in combined[:top_k]]
