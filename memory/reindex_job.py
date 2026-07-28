"""Background job that builds/rebuilds the FAISS vector index from long_term_memory."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from memory.embedding_service import EmbeddingService
    from memory.vector_store import VectorStore
    from memory.tiered_store import LongTermMemoryStore

logger = logging.getLogger("eva.reindex")


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return vec
    return vec / norm  # type: ignore[no-any-return]


def reindex_all(
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
    ltm_store: LongTermMemoryStore,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Fetch all active long_term_memory rows, encode, and rebuild FAISS index.

    Runs as a fire-and-forget background job. Non-blocking by design.
    """
    start = time.monotonic()
    rows = ltm_store.store.fetchall(
        "SELECT id, content FROM long_term_memory WHERE status = 'active'",
        (),
    )
    total = len(rows)
    if total == 0:
        logger.info("reindex: no active LTM rows to index")
        return {"indexed": 0, "skipped": 0, "duration_sec": 0.0}

    # Build fresh index
    from memory.vector_store import VectorStore as VS
    fresh = VS(dim=embedding_service.dim)
    indexed = 0

    for i in range(0, total, batch_size):
        batch = rows[i : i + batch_size]
        texts = [r["content"] for r in batch]
        ids = [r["id"] for r in batch]
        try:
            embeddings = embedding_service.encode(texts)
        except Exception:
            logger.exception("reindex encoding failed at batch %d", i // batch_size)
            continue

        items = [
            (mid, _l2_normalize(embeddings[j]))
            for j, mid in enumerate(ids)
        ]
        fresh.add_batch(items)
        indexed += len(items)

    # Atomically swap in the new index
    vector_store._index = fresh._index
    vector_store._id_to_idx = fresh._id_to_idx
    vector_store._idx_to_id = fresh._idx_to_id
    vector_store._next_idx = fresh._next_idx

    elapsed = time.monotonic() - start
    logger.info("reindex complete: %d/%d vectors in %.1fs", indexed, total, elapsed)
    return {"indexed": indexed, "skipped": total - indexed, "duration_sec": round(elapsed, 1)}
