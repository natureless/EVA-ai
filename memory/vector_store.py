"""FAISS flat index wrapper with ID mapping for vector storage and search."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("eva.vector")


class VectorStore:
    """Wraps a FAISS IndexFlatIP (inner product = cosine on normalized vectors)."""

    def __init__(self, dim: int = 384, index_path: Path | None = None) -> None:
        self._dim = dim
        self._index_path = index_path
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._next_idx = 0
        self._index = None  # created lazily on first add

    @property
    def _faiss_index(self):
        if self._index is None:
            import faiss
            self._index = faiss.IndexFlatIP(self._dim)
        return self._index

    def add(self, memory_id: str, embedding: np.ndarray) -> None:
        vec = embedding.astype(np.float32).reshape(1, -1)
        self._faiss_index.add(vec)
        idx = self._next_idx
        self._next_idx += 1
        self._id_to_idx[memory_id] = idx
        self._idx_to_id[idx] = memory_id

    def add_batch(self, items: list[tuple[str, np.ndarray]]) -> None:
        if not items:
            return
        ids, vecs = zip(*items)
        matrix = np.vstack([v.astype(np.float32).reshape(1, -1) for v in vecs])
        start_idx = self._next_idx
        self._faiss_index.add(matrix)
        for i, mid in enumerate(ids):
            idx = start_idx + i
            self._id_to_idx[mid] = idx
            self._idx_to_id[idx] = mid
        self._next_idx = start_idx + len(items)

    def search(self, query_vec: np.ndarray, k: int = 50) -> list[tuple[str, float]]:
        if self._next_idx == 0:
            return []
        q = query_vec.astype(np.float32).reshape(1, -1)
        scores, indices = self._faiss_index.search(q, min(k, self._next_idx))
        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            mid = self._idx_to_id.get(int(idx))
            if mid:
                results.append((mid, float(score)))
        return results

    def remove(self, memory_id: str) -> None:
        idx = self._id_to_idx.pop(memory_id, None)
        if idx is not None:
            self._idx_to_id.pop(idx, None)

    def save(self) -> None:
        if not self._index_path or self._index is None:
            return
        import faiss
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        map_path = self._index_path.with_suffix(".map.json")
        map_path.write_text(json.dumps({
            "id_to_idx": self._id_to_idx,
            "idx_to_id": {str(k): v for k, v in self._idx_to_id.items()},
            "next_idx": self._next_idx,
        }))
        logger.info("vector index saved (%d vectors)", self._next_idx)

    def load(self) -> None:
        if not self._index_path or not self._index_path.exists():
            return
        import faiss
        self._index = faiss.read_index(str(self._index_path))
        map_path = self._index_path.with_suffix(".map.json")
        if map_path.exists():
            data = json.loads(map_path.read_text())
            self._id_to_idx = data["id_to_idx"]
            self._idx_to_id = {int(k): v for k, v in data["idx_to_id"].items()}
            self._next_idx = data["next_idx"]
        logger.info("vector index loaded (%d vectors)", self._next_idx)

    def size(self) -> int:
        return self._next_idx
