"""Construction rollback and managed background work protect live storage."""

from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def prepared_bootstrap(tmp_path, monkeypatch):
    bootstrap = importlib.import_module("app.bootstrap")
    composition = importlib.import_module("app.composition")
    Settings = importlib.import_module("app.config").Settings
    config = Settings(
        _env_file=None,
        base_dir=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "eva.db",
        log_dir=tmp_path / "logs",
        template_dir=tmp_path / "templates",
        static_dir=tmp_path / "static",
        snapshot_dir=tmp_path / "snapshots",
        latest_snapshot_path=tmp_path / "snapshots" / "latest.json",
        profile_path=tmp_path / "profile.json",
        self_model_path=tmp_path / "self_model.json",
        embedding_provider="none",
        storage_backend="sqlite",
        enable_minimal_brain=False,
        enable_mvsc_pipeline=False,
        agent_worker_backend="thread",
        queue_poll_timeout_sec=0.01,
        scheduler_tick_interval_sec=60,
        scheduler_maintenance_interval_sec=60,
        scheduler_snapshot_interval_sec=60,
        github_api_token="",
        github_poll_repos="",
        env="dev",
    )
    monkeypatch.setattr(bootstrap, "settings", config)
    monkeypatch.setattr(bootstrap, "configure_logging", MagicMock())
    monkeypatch.setattr(bootstrap, "shutdown_logging", MagicMock())
    monkeypatch.setattr(
        bootstrap,
        "_run_boot_diagnostic",
        lambda _: SimpleNamespace(
            overall="healthy",
            score=100,
            failed_checks=lambda: [],
            to_dict=lambda: {"overall": "healthy", "score": 100},
        ),
    )
    monkeypatch.setattr(bootstrap, "_log_diagnostic", lambda _: None)
    stores, workers, starts = [], [], []
    make_store = composition._build_store
    make_worker = bootstrap.create_agent_worker_backend
    start_loop = bootstrap.CognitionLoop.start

    def tracked_store(*args, **kwargs):
        store = make_store(*args, **kwargs)
        store.close = MagicMock(wraps=store.close)
        stores.append(store)
        return store

    def tracked_worker(*args, **kwargs):
        worker = make_worker(*args, **kwargs)
        worker.shutdown = MagicMock(wraps=worker.shutdown)
        workers.append(worker)
        return worker

    def tracked_start(loop):
        starts.append(loop)
        return start_loop(loop)

    monkeypatch.setattr(composition, "_build_store", tracked_store)
    monkeypatch.setattr(bootstrap, "create_agent_worker_backend", tracked_worker)
    monkeypatch.setattr(bootstrap.CognitionLoop, "start", tracked_start)
    return SimpleNamespace(
        bootstrap=bootstrap,
        composition=composition,
        stores=stores,
        workers=workers,
        starts=starts,
    )


@pytest.mark.parametrize(
    "failure_stage", ["memory_factory", "identity", "consumer_assembly"]
)
def test_pre_controller_failure_closes_prepared_resources_once(
    prepared_bootstrap, monkeypatch, failure_stage
):
    prepared = prepared_bootstrap
    original = RuntimeError(f"injected {failure_stage} failure")

    def fail(*args, **kwargs):
        raise original

    if failure_stage == "memory_factory":
        monkeypatch.setattr(prepared.composition, "_build_vector_search", fail)
    elif failure_stage == "identity":
        monkeypatch.setattr(prepared.bootstrap, "build_identity", fail)
    else:
        monkeypatch.setattr(prepared.bootstrap, "HealthService", fail)

    with pytest.raises(RuntimeError) as raised:
        prepared.bootstrap.bootstrap_system()

    assert raised.value is original
    assert len(prepared.stores) == 1
    prepared.stores[0].close.assert_called_once()
    assert prepared.stores[0]._connections == []
    assert prepared.starts == []
    for worker in prepared.workers:
        worker.shutdown.assert_called_once()
    if failure_stage == "consumer_assembly":
        assert len(prepared.workers) == 1


def test_successful_controller_handoff_does_not_run_construction_cleanup(
    prepared_bootstrap,
):
    prepared = prepared_bootstrap
    container = prepared.bootstrap.bootstrap_system()
    try:
        assert container.runtime.controller.is_running
        assert len(prepared.starts) == 1
        prepared.stores[0].close.assert_not_called()
        prepared.workers[0].shutdown.assert_not_called()
        assert container.store.fetchone("SELECT 1 AS ready")["ready"] == 1
    finally:
        assert prepared.bootstrap.shutdown_system(container)
    prepared.stores[0].close.assert_called_once()
    prepared.workers[0].shutdown.assert_called_once()
    assert prepared.bootstrap.shutdown_system(container)
    prepared.stores[0].close.assert_called_once()
    prepared.workers[0].shutdown.assert_called_once()


def test_managed_reindex_keeps_storage_open_until_real_work_finishes(
    prepared_bootstrap, monkeypatch
):
    prepared = prepared_bootstrap
    container = prepared.bootstrap.bootstrap_system()
    entered, release = threading.Event(), threading.Event()

    def reindex(_embedding, _vector, tier):
        entered.set()
        assert release.wait(5.0)
        assert tier.store.fetchone("SELECT 1 AS still_open")["still_open"] == 1
        return {"indexed": 1}

    monkeypatch.setattr("memory.reindex_job.reindex_all", reindex)
    future = None
    try:
        # Only index staleness is synthesized; execution, worker accounting,
        # runtime shutdown and SQLite connection lifetime are real.
        count_store = SimpleNamespace(fetchall=lambda *_: [{"cnt": 1}])
        vector = SimpleNamespace(size=lambda: 0)
        future = prepared.composition.schedule_memory_reindex_if_stale(
            count_store,
            container.tiered_memory,
            container.runtime.worker_backend.submit,
            object(),
            vector,
        )
        assert future is not None
        assert entered.wait(1.0)
        assert container.runtime.worker_backend.stats["pending"] == 1
        assert container.runtime.controller.stop(timeout=0.01) is False
        prepared.stores[0].close.assert_not_called()
        prepared.workers[0].shutdown.assert_not_called()
        assert container.store.fetchone("SELECT 1 AS ready")["ready"] == 1
    finally:
        release.set()
        if future is not None:
            future.result(timeout=1.0)
        assert container.runtime.controller.stop(timeout=1.0)
    prepared.stores[0].close.assert_called_once()
    prepared.workers[0].shutdown.assert_called_once()


def test_processing_episode_connection_obeys_real_worker_lifetime(prepared_bootstrap):
    prepared = prepared_bootstrap
    prepared.bootstrap.settings.enable_processing_episodes = True
    container = prepared.bootstrap.bootstrap_system()
    journal = container.integrations.processing_episodes
    entered, release = threading.Event(), threading.Event()

    def active():
        entered.set()
        assert release.wait(5)
        assert journal.stats()["processing"] == 0

    future = container.runtime.worker_backend.submit(active)
    try:
        assert entered.wait(1)
        assert container.runtime.controller.stop(timeout=0.01) is False
        assert not journal._closed
    finally:
        release.set()
        future.result(timeout=2)
        assert container.runtime.controller.stop(timeout=2)
    assert journal._closed


def test_failed_construction_closes_processing_episode_connection(
    prepared_bootstrap, monkeypatch
):
    prepared = prepared_bootstrap
    prepared.bootstrap.settings.enable_processing_episodes = True
    built = []
    factory = prepared.bootstrap.build_processing_episodes

    def tracked(config, **kwargs):
        store = factory(config, **kwargs)
        built.append(store)
        return store

    monkeypatch.setattr(prepared.bootstrap, "build_processing_episodes", tracked)
    monkeypatch.setattr(
        prepared.bootstrap,
        "HealthService",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("injected construction failure")
        ),
    )
    with pytest.raises(RuntimeError):
        prepared.bootstrap.bootstrap_system()
    assert len(built) == 1 and built[0]._closed


def test_failed_receipt_delivery_prevents_consumers_and_closes_both_connections(
    prepared_bootstrap, monkeypatch
):
    prepared = prepared_bootstrap
    prepared.bootstrap.settings.enable_processing_episodes = True
    prepared.bootstrap.settings.enable_business_goals = True
    built = []
    goals_factory = prepared.bootstrap.build_business_goals
    journal_factory = prepared.bootstrap.build_processing_episodes

    def tracked_goals(*args, **kwargs):
        store = goals_factory(*args, **kwargs)
        built.append(store)
        return store

    def tracked_journal(*args, **kwargs):
        journal = journal_factory(*args, **kwargs)
        built.append(journal)

        def fail(_sink):
            raise RuntimeError("injected durable receipt delivery failure")

        monkeypatch.setattr(journal, "attach_receipt_sink", fail)
        return journal

    monkeypatch.setattr(prepared.bootstrap, "build_business_goals", tracked_goals)
    monkeypatch.setattr(
        prepared.bootstrap, "build_processing_episodes", tracked_journal
    )
    with pytest.raises(RuntimeError, match="durable receipt delivery failure"):
        prepared.bootstrap.bootstrap_system()
    assert len(built) == 2 and all(store._closed for store in built)
    assert prepared.starts == []
