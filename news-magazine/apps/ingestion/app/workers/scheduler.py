"""
Background scheduler.

Each enabled+allowed source is polled on its own configurable interval
(never a fixed global interval, and never sub-minute). A source that
fails repeatedly backs off exponentially instead of being retried on the
normal schedule.

What Stage 1 got wrong, and what is fixed here
----------------------------------------------

1. `start()` fired `refresh_jobs()` as a fire-and-forget task
   (`asyncio.get_event_loop().create_task`). If the sources query raised
   — Supabase unreachable at boot, migration not applied, bad
   credentials — the exception was swallowed into a dead task and the
   daemon ran forever ingesting nothing, reporting healthy. The
   bootstrap is now awaited, retried, and its failure is fatal-by-log.

2. `refresh_jobs()` ran exactly once at startup, so editing a source
   row in the admin panel had no effect until a redeploy. It now
   re-runs on a fixed cadence.

3. Exponential backoff passed a float epoch to an APScheduler `date`
   trigger, which requires a datetime — the call raised TypeError and
   the retry job was never created. Now a real datetime.

4. Backoff added a one-shot job but left the interval job running, so a
   failing source was retried on its normal cadence anyway. Backoff now
   pauses the interval job and resumes it when the retry fires.

5. The scheduler lived only inside the FastAPI process. Running uvicorn
   with `--workers N` or `--reload` starts N schedulers polling the same
   sources concurrently. `worker.py` is the standalone entrypoint for
   running exactly one daemon; main.py keeps an embedded scheduler for
   single-process local development and refuses to run one when
   WORKER_MODE is set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import get_db
from app.services.ingestion import run_source_sync

logger = logging.getLogger("scheduler")

MAX_BACKOFF_SECONDS = 6 * 60 * 60  # cap at 6 hours
REFRESH_INTERVAL_SECONDS = 300  # pick up source-table edits every 5 min

# A sync that has been 'running' longer than this was cancelled, not slow.
# Generous enough to clear the slowest legitimate run (27 outlets, each
# with its own fetch timeout and media enrichment).
ORPHANED_SYNC_LOG_SECONDS = 3600
MIN_POLL_SECONDS = 60  # never poll a source faster than once a minute

_consecutive_failures: dict[str, int] = {}
_scheduler: AsyncIOScheduler | None = None


def job_id(source_id: str) -> str:
    return f"sync-{source_id}"


def backoff_job_id(source_id: str) -> str:
    return f"backoff-{source_id}"


# =============================================================
# SINGLE SOURCE
# =============================================================

async def _sync_one(source_id: str) -> None:
    """Poll one source and apply backoff on failure."""
    from app.services.ingestion import db_call

    db = get_db()

    try:
        response = await db_call(
            lambda: db.table("sources").select("*").eq("id", source_id).limit(1).execute()
        )
    except Exception:
        # Database down. Do NOT count this against the source — the
        # source is fine, we simply cannot reach the database.
        logger.exception("Scheduler: could not read source %s from database", source_id)
        return

    source_row = (response.data or [None])[0]
    if not source_row:
        logger.debug("Scheduler: source %s no longer exists; removing job", source_id)
        _remove_jobs(source_id)
        return

    if not source_row.get("enabled") or not source_row.get("allowed"):
        _remove_jobs(source_id)
        return

    try:
        result = await run_source_sync(source_row)
    except asyncio.CancelledError:
        # Cancellation is not a failure of the source — it is the task
        # being torn down (a deploy, a shutdown, an explicit cancel).
        # asyncio.CancelledError is a BaseException in Python 3.8+, so
        # the `except Exception` below never sees it; it used to escape
        # straight into APScheduler as an unhandled job exception, which
        # meant backoff never engaged and the sync log stayed 'running'
        # forever.
        #
        # Re-raise rather than swallow: a swallowed cancellation breaks
        # structured concurrency, because the caller still believes the
        # task is alive.
        logger.warning(
            "Scheduler: sync of %s was cancelled mid-run.",
            source_row.get("name"),
        )
        raise
    except Exception:
        # run_source_sync is written not to raise, but a bug in it must
        # not kill the scheduler task and silently stop this source.
        logger.exception("Scheduler: unhandled error syncing %s", source_row.get("name"))
        result = None

    if result is None or (result.errors > 0 and result.new == 0):
        failures = _consecutive_failures.get(source_id, 0) + 1
        _consecutive_failures[source_id] = failures

        interval = int(source_row.get("poll_interval_seconds") or 900)
        delay = min(interval * (2 ** failures), MAX_BACKOFF_SECONDS)

        logger.warning(
            "Source %s failed (%s consecutive); backing off %ss before retry.",
            source_row.get("name"), failures, delay,
        )
        _apply_backoff(source_id, delay)
    else:
        _consecutive_failures.pop(source_id, None)


def _apply_backoff(source_id: str, delay_seconds: int) -> None:
    """
    Pause the interval job and schedule one retry after the delay.

    Stage 1 added the retry job but left the interval job running, so
    the source kept being retried on its normal cadence and the backoff
    had no effect at all.
    """
    scheduler = get_scheduler()

    try:
        interval_job = scheduler.get_job(job_id(source_id))
        if interval_job:
            interval_job.pause()
    except Exception:
        logger.debug("Could not pause interval job for %s", source_id)

    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

    try:
        scheduler.add_job(
            _resume_after_backoff,
            trigger="date",
            run_date=run_at,  # a datetime; Stage 1 passed a float epoch here
            args=[source_id],
            id=backoff_job_id(source_id),
            replace_existing=True,
            misfire_grace_time=None,
        )
    except Exception:
        logger.exception("Could not schedule backoff retry for %s", source_id)


async def _resume_after_backoff(source_id: str) -> None:
    """Retry now, then put the source back on its normal interval."""
    scheduler = get_scheduler()

    try:
        interval_job = scheduler.get_job(job_id(source_id))
        if interval_job:
            interval_job.resume()
        else:
            # The interval job was removed while we were backing off.
            await _sync_one(source_id)
            return
    except Exception:
        logger.debug("Could not resume interval job for %s", source_id)

    await _sync_one(source_id)


def _remove_jobs(source_id: str) -> None:
    """Drop every job belonging to a source that is disabled or gone."""
    scheduler = get_scheduler()
    for identifier in (job_id(source_id), backoff_job_id(source_id)):
        try:
            job = scheduler.get_job(identifier)
            if job:
                job.remove()
        except Exception:
            pass
    _consecutive_failures.pop(source_id, None)


# =============================================================
# JOB REGISTRY
# =============================================================

async def refresh_jobs() -> int:
    """
    Re-read the sources table and (re)schedule one job per active source.

    Called on startup and then every REFRESH_INTERVAL_SECONDS so an
    admin editing a source row sees the change without a redeploy.
    Returns the number of active sources scheduled.
    """
    from app.services.ingestion import db_call

    db = get_db()

    try:
        response = await db_call(
            lambda: db.table("sources")
            .select("id, name, enabled, allowed, poll_interval_seconds")
            .eq("enabled", True)
            .eq("allowed", True)
            .execute()
        )
        sources = response.data or []
    except Exception:
        logger.exception(
            "Scheduler: could not read the sources table. No jobs will be "
            "scheduled until the next refresh."
        )
        return 0

    scheduler = get_scheduler()
    active_ids = {row["id"] for row in sources}

    # Remove jobs for sources that were disabled or deleted.
    for job in scheduler.get_jobs():
        if job.id.startswith("sync-"):
            source_id = job.id.removeprefix("sync-")
            if source_id not in active_ids:
                try:
                    job.remove()
                except Exception:
                    pass
        elif job.id.startswith("backoff-"):
            source_id = job.id.removeprefix("backoff-")
            if source_id not in active_ids:
                try:
                    job.remove()
                except Exception:
                    pass

    # (Re)schedule every active source.
    for source in sources:
        try:
            interval = max(
                int(source.get("poll_interval_seconds") or 900), MIN_POLL_SECONDS
            )
            scheduler.add_job(
                _sync_one,
                trigger=IntervalTrigger(seconds=interval),
                args=[source["id"]],
                id=job_id(source["id"]),
                replace_existing=True,
                max_instances=1,   # never two syncs of one source at once
                coalesce=True,    # collapse missed runs into one
                misfire_grace_time=120,
                next_run_time=datetime.now(timezone.utc) + timedelta(
                    seconds=_stagger(source["id"])
                ),
            )
            _consecutive_failures.pop(source["id"], None)
        except Exception:
            logger.exception(
                "Scheduler: could not schedule source %s", source.get("name")
            )

    logger.info("Scheduler: %d active source(s) scheduled.", len(sources))
    return len(sources)


def _stagger(source_id: str) -> float:
    """
    Spread first runs across the interval instead of firing 27 polls in
    the same second, which looks like a traffic spike to every outlet at
    once. Deterministic per source so restarts do not reshuffle.
    """
    import hashlib

    digest = hashlib.sha256(str(source_id).encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:2], "big") % 30
    return float(bucket)


async def _periodic_refresh() -> None:
    """Re-read the sources table forever, so admin edits take effect."""
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            await _close_orphaned_sync_logs()
            await refresh_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler: periodic job refresh failed")


async def _close_orphaned_sync_logs() -> int:
    """
    Mark sync-log rows abandoned by a cancelled run as failed.

    A run that is cancelled mid-flight never reaches _finish_sync_log,
    so its row keeps status 'running' for ever. The admin surface counts
    failures from those rows, so one deploy was enough to make the
    health of the pipeline permanently unreadable.

    Only rows older than the longest plausible run are touched, so a sync
    that is genuinely still in progress is never closed out from under
    itself.
    """
    from app.services.ingestion import db_call

    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ORPHANED_SYNC_LOG_SECONDS)

    try:
        response = await db_call(
            lambda: db.table("source_sync_logs")
            .update({"status": "failed", "finished_at": cutoff.isoformat()})
            .eq("status", "running")
            .lt("started_at", cutoff.isoformat())
            .execute()
        )
        return len(response.data or [])
    except Exception:
        logger.debug("Could not close orphaned sync log rows")
        return 0


# =============================================================
# LIFECYCLE
# =============================================================

def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone="UTC",
            job_defaults={
                # A job that throws must be logged loudly and the
                # scheduler must survive it.
                "misfire_grace_time": 120,
                "max_instances": 1,
                "coalesce": True,
            },
        )
    return _scheduler


async def start() -> bool:
    """
    Start the scheduler and bootstrap jobs.

    Awaits the first refresh so a failure at boot is visible immediately
    rather than swallowed into an orphaned task. Returns True when at
    least one source was scheduled.
    """
    scheduler = get_scheduler()

    if not scheduler.running:
        scheduler.start()

    scheduled = await refresh_jobs()

    if scheduled == 0:
        logger.warning(
            "Scheduler: 0 sources scheduled. Either the sources table is "
            "empty (apply migration 0008_seed_nepali_sources.sql) or no "
            "source has enabled=true AND allowed=true."
        )

    # Keep the job registry in sync with the sources table.
    scheduler.add_job(
        _periodic_refresh,
        trigger="interval",
        seconds=REFRESH_INTERVAL_SECONDS,
        id="refresh-jobs",
        replace_existing=True,
    )

    return scheduled > 0


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.debug("Scheduler shutdown raised; ignoring")