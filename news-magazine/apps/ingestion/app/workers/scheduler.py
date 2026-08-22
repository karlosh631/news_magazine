"""
Background scheduler. Each enabled+allowed source is polled on its own
configurable interval (never a fixed global interval, and never
sub-minute — see spec section 48). A source that fails repeatedly backs
off exponentially instead of being retried on the normal schedule.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import get_db
from app.services.ingestion import run_source_sync

logger = logging.getLogger("scheduler")

MAX_BACKOFF_SECONDS = 6 * 60 * 60  # cap at 6 hours
_consecutive_failures: dict[str, int] = {}


async def _sync_one(source_id: str) -> None:
    db = get_db()
    res = db.table("sources").select("*").eq("id", source_id).limit(1).execute()
    if not res.data:
        return
    source_row = res.data[0]

    if not source_row.get("enabled") or not source_row.get("allowed"):
        return

    result = await run_source_sync(source_row)

    if result.errors > 0 and result.new == 0:
        _consecutive_failures[source_id] = _consecutive_failures.get(source_id, 0) + 1
        backoff = min(
            source_row["poll_interval_seconds"] * (2 ** _consecutive_failures[source_id]),
            MAX_BACKOFF_SECONDS,
        )
        logger.warning(
            "Source %s failed (%s consecutive). Backing off %ss before retry.",
            source_row["name"], _consecutive_failures[source_id], backoff,
        )
        scheduler = get_scheduler()
        scheduler.add_job(
            _sync_one, "date",
            run_date=datetime.now(timezone.utc).timestamp() + backoff,
            args=[source_id], id=f"backoff-{source_id}", replace_existing=True,
        )
    else:
        _consecutive_failures[source_id] = 0


_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def refresh_jobs() -> None:
    """
    Re-reads the sources table and (re)schedules a periodic job per
    enabled+allowed source, using that source's own poll_interval_seconds.
    Call this on startup and whenever an admin edits a source (or simply
    on a short fixed cadence, e.g. every 5 minutes, to pick up changes).
    """
    db = get_db()
    sources = db.table("sources").select("*").eq("enabled", True).eq("allowed", True).execute().data or []
    scheduler = get_scheduler()

    active_ids = {s["id"] for s in sources}
    for job in scheduler.get_jobs():
        if job.id.startswith("sync-") and job.id.removeprefix("sync-") not in active_ids:
            job.remove()

    for source in sources:
        job_id = f"sync-{source['id']}"
        scheduler.add_job(
            _sync_one,
            "interval",
            seconds=source["poll_interval_seconds"],
            args=[source["id"]],
            id=job_id,
            replace_existing=True,
            max_instances=1,  # never run two syncs of the same source concurrently
            coalesce=True,
        )
    logger.info("Scheduled %d active sources.", len(sources))


def start() -> None:
    scheduler = get_scheduler()
    scheduler.start()
    asyncio.get_event_loop().create_task(refresh_jobs())
