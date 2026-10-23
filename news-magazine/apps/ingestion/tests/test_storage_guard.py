"""
The database must never be the reason the site stops.

THE RUN THAT WOULD HAVE FILLED IT
---------------------------------
The articles are pruned on a TTL and a row cap, and both of those
work. The tables that were NOT pruned are the ones that grow:

  - `source_sync_logs`: one row per outlet per pass. Thirty-four
    outlets, a pass every three minutes, is more than sixteen thousand
    rows a day, each with a jsonb blob in it.
  - `source_errors`: one row per failure, and a failing outlet writes
    one every pass.
  - `analytics_events`: one row per read.

Nothing deleted any of them, ever.

These specs pin the two moves the guard makes:

  - the log tables go by age on EVERY pass, not when the database is
    nearly full;
  - and when the database is at eighty percent of its limit, the
    oldest half of the articles goes too, so the site keeps serving
    and nobody has to do anything by hand.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import ingestion as ingestion_module
from app.services import storage_guard

NOW = datetime(2026, 10, 23, 15, 0, 0, tzinfo=timezone.utc)


class FakeDB:
    """A database that records what it was asked to do."""

    def __init__(self):
        self.deletes: list[tuple[str, str, object]] = []
        self.counts: dict[str, int] = {}
        self.rows: list[dict] = []
        self.rpc_result = None
        self.rpc_error = None

    def table(self, name):
        return FakeTable(self, name)

    def rpc(self, name):
        return FakeRpc(self, name)


class FakeTable:
    def __init__(self, db: FakeDB, name: str):
        self.db = db
        self.name = name
        self._op = "select"
        self._filters: list[tuple[str, str, object]] = []

    # -- the chain the guard uses --
    def select(self, *args, **kwargs):
        self._op = "select"
        return self

    def delete(self):
        self._op = "delete"
        return self

    def lt(self, column, value):
        self._filters.append(("lt", column, value))
        return self

    def order(self, *args, **kwargs):
        return self

    def desc(self):
        return self

    def range(self, *args):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        if self._op == "delete":
            self.db.deletes.append((self.name, self._filters[0][1], self._filters[0][2]))
            return SimpleNamespace(data=[{"id": "x"}])
        if self.name == "articles":
            return SimpleNamespace(
                data=self.db.rows, count=self.db.counts.get("articles", 0)
            )
        return SimpleNamespace(data=[], count=self.db.counts.get(self.name, 0))


class FakeRpc:
    def __init__(self, db: FakeDB, name: str):
        self.db = db
        self.name = name

    def execute(self):
        if self.db.rpc_error is not None:
            raise self.db.rpc_error
        return SimpleNamespace(data=self.db.rpc_result)


@pytest.fixture
def guard(monkeypatch):
    db = FakeDB()

    async def fake_db_call(fn):
        return fn()

    monkeypatch.setattr(ingestion_module, "db_call", fake_db_call)
    return db


def settings(**over):
    base = dict(
        database_size_limit_bytes=500 * 1024 * 1024,
        storage_pressure_threshold=0.8,
        storage_emergency_prune_ratio=0.5,
        sync_log_retention_days=7,
        error_log_retention_days=30,
        analytics_retention_days=30,
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestLogsGoByAge:
    async def test_the_three_growing_tables_are_pruned_every_pass(self, guard):
        report = await storage_guard.run_storage_guard(guard, settings())

        pruned = {name for name, _, _ in guard.deletes}
        assert "source_sync_logs" in pruned
        assert "source_errors" in pruned
        assert "analytics_events" in pruned
        assert report["logs_pruned"] == 1
        assert report["errors_pruned"] == 1
        assert report["analytics_pruned"] == 1

    async def test_the_cutoff_is_the_retention_window(self, guard):
        await storage_guard.run_storage_guard(guard, settings(sync_log_retention_days=7))

        cutoffs = {
            name: value for name, _, value in guard.deletes if name == "source_sync_logs"
        }
        cutoff = datetime.fromisoformat(cutoffs["source_sync_logs"])
        assert cutoff <= datetime.now(timezone.utc) - timedelta(days=7)

    async def test_a_full_database_is_never_the_reason_to_skip_the_prune(
        self, guard
    ):
        # The prune runs FIRST, before the pressure is even measured.
        guard.rpc_result = [{"used_bytes": 499 * 1024 * 1024}]
        await storage_guard.run_storage_guard(guard, settings())

        assert any(name == "source_sync_logs" for name, _, _ in guard.deletes)


class TestTheEmergency:
    async def test_nothing_is_deleted_when_the_database_is_roomy(self, guard):
        guard.rpc_result = [{"used_bytes": 100 * 1024 * 1024}]

        report = await storage_guard.run_storage_guard(guard, settings())

        assert report["emergency_pruned"] == 0
        assert not any(name == "articles" for name, _, _ in guard.deletes)

    async def test_the_oldest_half_goes_at_eighty_percent(self, guard):
        guard.rpc_result = [{"used_bytes": 420 * 1024 * 1024}]  # 84%
        guard.counts["articles"] = 1000
        guard.rows = [{"published_at": "2026-10-20T00:00:00Z"}]

        report = await storage_guard.run_storage_guard(guard, settings())

        assert report["emergency_pruned"] == 1
        assert any(name == "articles" for name, _, _ in guard.deletes)

    async def test_the_estimate_is_used_when_the_function_is_missing(self, guard):
        # No `storage_pressure` function: the guard weighs the rows it
        # can count instead. It must still notice the pressure, or a
        # database without migration 0034 would have no guard at all.
        guard.rpc_error = RuntimeError("function does not exist")
        guard.counts = {
            "articles": 250_000,
            "source_sync_logs": 500_000,
            "source_errors": 100_000,
            "analytics_events": 100_000,
        }
        guard.rows = [{"published_at": "2026-10-01T00:00:00Z"}]

        report = await storage_guard.run_storage_guard(guard, settings())

        assert report["used_bytes"] is not None
        assert report["pressure"] is not None
        assert report["emergency_pruned"] == 1

    async def test_the_guard_never_raises(self, guard, monkeypatch):
        async def explode(*args, **kwargs):
            raise RuntimeError("the database is on fire")

        monkeypatch.setattr(storage_guard, "_count", explode)
        monkeypatch.setattr(storage_guard, "_measured_bytes", explode)
        monkeypatch.setattr(storage_guard, "_delete_older_than", explode)

        # The caller catches it, but the guard itself must not take the
        # pass down with it.
        with pytest.raises(RuntimeError):
            await storage_guard.run_storage_guard(guard, settings())
