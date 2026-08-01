"""SemanticCache — 记忆检索结果缓存。

对高频查询缓存检索结果，减少重复的 FTS5 + 向量搜索。
使用 LRU 淘汰策略，缓存键为查询的归一化形式。

特性:
- 线程安全 (RLock)
- TTL 过期 (默认 60s)
- LRU 淘汰 (默认 100 条)
- 命中率统计
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any


class SemanticCache:
    """记忆检索结果的 LRU 缓存。

    Usage::

        cache = SemanticCache(max_size=100, ttl_sec=60)
        result = cache.get("user query")
        if result is None:
            result = do_expensive_search("user query")
            cache.put("user query", result)
    """

    def __init__(self, max_size: int = 100, ttl_sec: float = 60.0) -> None:
        self._max_size = max_size
        self._ttl_sec = ttl_sec
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._access_order: list[str] = []  # LRU: most recent at end
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _normalize(query: str) -> str:
        """归一化查询字符串为缓存键。"""
        normalized = " ".join(query.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def get(self, query: str) -> list[dict[str, Any]] | None:
        """获取缓存的检索结果。

        Returns:
            结果列表，或 None（缓存未命中）
        """
        key = self._normalize(query)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            ts, results = entry
            if time.time() - ts > self._ttl_sec:
                # 过期
                del self._cache[key]
                self._access_order.remove(key)
                self._misses += 1
                return None

            # LRU: move to end
            self._access_order.remove(key)
            self._access_order.append(key)
            self._hits += 1
            return results

    def put(self, query: str, results: list[dict[str, Any]]) -> None:
        """缓存检索结果。"""
        key = self._normalize(query)
        with self._lock:
            # 淘汰最旧的条目
            if key not in self._cache and len(self._cache) >= self._max_size:
                oldest = self._access_order.pop(0)
                del self._cache[oldest]

            self._cache[key] = (time.time(), results)
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)

    def invalidate(self, query: str | None = None) -> int:
        """使缓存失效。

        Args:
            query: 特定查询（None = 清空全部）

        Returns:
            失效条目数
        """
        with self._lock:
            if query is None:
                count = len(self._cache)
                self._cache.clear()
                self._access_order.clear()
                return count
            key = self._normalize(query)
            if key in self._cache:
                del self._cache[key]
                self._access_order.remove(key)
                return 1
            return 0

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self.hit_rate, 4),
                "ttl_sec": self._ttl_sec,
            }
