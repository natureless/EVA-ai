"""Performance benchmark unit tests."""

import os
import tempfile
from pathlib import Path


class TestLayerBenchmarks:
    """Verify all internal layer benchmarks work without HTTP."""

    def test_importance_scorer_bench(self):
        from memory.importance_scorer import ImportanceFeatures, ImportanceScorer
        import time

        scorer = ImportanceScorer()
        feats = ImportanceFeatures(
            user_explicit=True, goal_related=True, self_model_delta=0.5,
        )
        start = time.perf_counter()
        for _ in range(1000):
            scorer.score(feats)
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 500  # should be fast

    def test_policy_evaluate_bench(self):
        from core.policy_engine import PolicyEngine
        import time

        pe = PolicyEngine()
        start = time.perf_counter()
        for _ in range(500):
            pe.evaluate("user_message", {"source": "user"})
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 500

    def test_world_model_upsert_bench(self):
        from world.world_model import WorldModelGraph
        import time

        wm = WorldModelGraph()
        start = time.perf_counter()
        for i in range(1000):
            wm.upsert_entity("task", f"Task {i}", {"status": "active"})
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 500
        assert wm.entity_count == 1000

    def test_entity_extraction_bench(self):
        from core.entity_extractor import entity_extractor
        import time

        text = "/task: fix login bug\nTODO: write tests\nassigned to @bob"
        start = time.perf_counter()
        for _ in range(500):
            entity_extractor.extract_entities(text)
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 500

    def test_memory_ingest_bench(self):
        from memory.tiered_store import TieredMemoryManager
        from memory.sqlite_store import SQLiteStore
        import time

        tmp = tempfile.mkdtemp()
        store = SQLiteStore(Path(os.path.join(tmp, "bench.db")))
        store.init_db()
        tm = TieredMemoryManager(store)

        start = time.perf_counter()
        for i in range(200):
            tm.ingest(f"entry {i}", importance=0.6, source="test")
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 2000  # SQLite I/O overhead on Windows

    def test_diagnostics_bench(self):
        from runtime.diagnostics import SystemDiagnostic
        from memory.tiered_store import TieredMemoryManager
        from memory.sqlite_store import SQLiteStore
        import time

        tmp = tempfile.mkdtemp()
        store = SQLiteStore(Path(os.path.join(tmp, "bench.db")))
        store.init_db()
        tm = TieredMemoryManager(store)
        diag = SystemDiagnostic()
        state = {
            "pending_events": 0, "pending_results": 0,
            "policy_state": {"state_machine": {"current": "dormant"}},
        }

        start = time.perf_counter()
        for _ in range(10):
            diag.run_full(store, tm, state)
        duration_ms = (time.perf_counter() - start) * 1000
        assert duration_ms < 2000

    def test_no_regression_on_existing_tests(self):
        """Sanity: benchmark module imports cleanly."""
        # This test just verifies the module structure
        import scripts.benchmark  # noqa

    def test_benchmark_stats_helper(self):
        from scripts.benchmark import BenchmarkRunner
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        stats = BenchmarkRunner._stats("test", values)
        assert stats["count"] == 10
        assert stats["mean_ms"] == 55.0
        assert stats["p50_ms"] == 60.0  # 10 items: sorted[5] = 60
        assert stats["min_ms"] == 10.0
        assert stats["max_ms"] == 100.0
