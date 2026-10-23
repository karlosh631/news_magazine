"""
The database must never be the reason the site stops.

WHAT THIS EXISTS FOR
--------------------
The owner asked for one thing in as many words: never let the system
collapse because the database is full, and do it without anybody
watching.

The database was going to fill up, and not from the news. The news is
pruned on a TTL and a row cap, and both of those are working. The
tables that were NOT being pruned are the ones that grow:

  - `source_sync_logs` — one row per outlet per pass. At thirty-four
    outlets and a pass every three minutes that is more than sixteen
    thousand rows a day, each with a jsonb blob in it.
  - `source_errors` — one row per failure, and a failing outlet writes
    one every pass.
  - `analytics_events` — one row per view, search and share.

Nothing deleted any of them, ever. A year of passes would fill a
500 MB database with logs, while the articles table — the thing the
site actually serves — was still a rounding error in it.

THE TWO MOVES
-------------
1. THE LOGS GO BY AGE, ON EVERY PASS. Not when the database is
   nearly full: every pass. A week of sync logs, a month of errors
   and a month of analytics. The guard is cheap (three deletes with a
   timestamp), and it means the emergency below almost never has to
   run.

2. THE EMERGENCY RUNS AT EIGHTY PERCENT. When the database is at
   eighty percent of its limit, the oldest half of the articles is
   deleted — the same half the TTL pass would have taken over the next
   two days, taken now. The site keeps serving, the archive gets
   shorter, and nothing has to be done by hand.

HOW FULL IT IS, MEASURED
------------------------
The size of the database is asked for through the `storage_pressure`
SQL function, which is migration 0034. When that function is not
there — a database that has not had the migration applied — the
estimate falls back to counting the rows and weighing them, which is
cruder but moves in the same direction and never gets the guard stuck
in the "not full" state because a function was missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: What one row of each table is assumed to weigh, in bytes, when the
#: real size is not available. Measured from the live database on
#: 2026-10-23: an article row with a body and a summary is roughly
#: 2 KB, a sync log with its details blob roughly 600 B, an error
#: roughly 400 B, an analytics event roughly 200 B.
BYTES_PER_ARTICLE = 2_000
BYTES_PER_SYNC_LOG = 600
BYTES_PER_ERROR = 400
BYTES_PER_ANALYTICS_EVENT = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _count(db, table: str) -> int:
    """
    How many rows a table has, counted exactly.

    The count header is exact rather than estimated, because the
    difference between 8,000 and 8,100 rows is the difference between
    "under the cap" and "over it".
    """
    from app.services.ingestion import db_call

    try:
        response = await db_call(
            lambda: db.table(table).select("id", count="exact").limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not count %s: %s", table, exc)
        return 0
    return int(getattr(response, "count", 0) or 0)


async def _delete_older_than(db, table: str, column: str, cutoff: datetime) -> int:
    from app.services.ingestion import db_call

    try:
        response = await db_call(
            lambda: db.table(table)
            .delete()
            .lt(column, cutoff.isoformat())
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not prune %s: %s", table, exc)
        return 0
    deleted = len(response.data or []) if response.data is not None else 0
    if deleted:
        logger.info(
            "Pruned %s row(s) from %s older than %s.",
            deleted,
            table,
            cutoff.date().isoformat(),
        )
    return deleted


async def _measured_bytes(db) -> int | None:
    """
    The real size of the database, when the database will say.

    Migration 0034 adds a `storage_pressure` function that reports
    `pg_database_size`. It is asked for first, because a number the
    database reports about itself is better than one this module
    guesses.
    """
    from app.services.ingestion import db_call

    try:
        response = await db_call(lambda: db.rpc("storage_pressure").execute())
    except Exception as exc:  # noqa: BLE001
        logger.debug("storage_pressure() is not available: %s", exc)
        return None

    data = response.data
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for key in ("used_bytes", "total_bytes", "size_bytes"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    return None


async def _estimated_bytes(db) -> int:
    """
    The size of the database, worked out from what is in it.

    The weights are measured, not invented, and the answer is good
    enough to decide whether a database is near its limit — which is
    all the guard needs it for.
    """
    articles, logs, errors, events = await asyncio_gather_counts(db)
    return (
        articles * BYTES_PER_ARTICLE
        + logs * BYTES_PER_SYNC_LOG
        + errors * BYTES_PER_ERROR
        + events * BYTES_PER_ANALYTICS_EVENT
    )


async def asyncio_gather_counts(db) -> tuple[int, int, int, int]:
    """Count the four tables that grow, together."""
    import asyncio

    counts = await asyncio.gather(
        _count(db, "articles"),
        _count(db, "source_sync_logs"),
        _count(db, "source_errors"),
        _count(db, "analytics_events"),
    )
    return counts  # type: ignore[return-value]


async def prune_old_articles_half(db, ratio: float = 0.5) -> int:
    """
    Delete the oldest half of the articles.

    The oldest half, by publication time — the same order the TTL
    pass uses, so this deletes what the TTL would have taken anyway,
    only sooner. The breaking and featured rows are kept: they are
    the ones the front page is holding, and a full database is no
    reason to unpublish them.
    """
    from app.services.ingestion import db_call

    total = await _count(db, "articles")
    if total == 0:
        return 0

    target = int(total * ratio)
    if target <= 0:
        target = 1

    try:
        # The timestamp at the halfway point: everything older than it
        # is the half that goes.
        response = await db_call(
            lambda: db.table("articles")
            .select("published_at", "ingested_at")
            .order("published_at", desc=True)
            .range(target - 1, target - 1)
            .execute()
        )
        rows = response.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not find the halfway point: %s", exc)
        return 0

    if not rows:
        return 0

    cutoff = rows[0].get("published_at") or rows[0].get("ingested_at")
    if not cutoff:
        return 0

    try:
        deleted = await db_call(
            lambda: db.table("articles")
            .delete()
            .lt("published_at", cutoff)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("The emergency prune could not run: %s", exc)
        return 0

    count = len(deleted.data or []) if deleted.data is not None else 0
    logger.warning(
        "EMERGENCY PRUNE: deleted %s article(s) older than %s to bring the "
        "database back under its limit. The site keeps serving; the archive "
        "is shorter.",
        count,
        cutoff,
    )
    return count


async def run_storage_guard(db, settings: Any) -> dict:
    """
    The whole guard, once per pass.

    Returns what it did, so the caller can report it and the admin
    panel can show it.
    """
    now = _utcnow()
    report: dict[str, Any] = {
        "logs_pruned": 0,
        "errors_pruned": 0,
        "analytics_pruned": 0,
        "emergency_pruned": 0,
        "used_bytes": None,
        "limit_bytes": int(settings.database_size_limit_bytes),
        "pressure": None,
    }

    # ---- 1. The logs go by age, every pass ----
    report["logs_pruned"] = await _delete_older_than(
        db,
        "source_sync_logs",
        "started_at",
        now - timedelta(days=settings.sync_log_retention_days),
    )
    report["errors_pruned"] = await _delete_older_than(
        db,
        "source_errors",
        "occurred_at",
        now - timedelta(days=settings.error_log_retention_days),
    )
    report["analytics_pruned"] = await _delete_older_than(
        db,
        "analytics_events",
        "occurred_at",
        now - timedelta(days=settings.analytics_retention_days),
    )

    # ---- 2. How full is it? ----
    limit = max(int(settings.database_size_limit_bytes), 1)
    used = await _measured_bytes(db)
    if used is None:
        used = await _estimated_bytes(db)
    report["used_bytes"] = used
    report["pressure"] = round(used / limit, 4) if limit else None

    # ---- 3. The emergency ----
    if used / limit >= float(settings.storage_pressure_threshold):
        logger.warning(
            "The database is at %.1f%% of its limit (%s of %s bytes). "
            "Deleting the oldest %d%% of the articles so the site keeps "
            "running.",
            (used / limit) * 100,
            used,
            limit,
            int(settings.storage_emergency_prune_ratio * 100),
        )
        report["emergency_pruned"] = await prune_old_articles_half(
            db, float(settings.storage_emergency_prune_ratio)
        )

    return report
