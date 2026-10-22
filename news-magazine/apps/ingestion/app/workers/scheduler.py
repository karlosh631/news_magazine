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
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    DEFAULT_MAX_REELS_PER_CYCLE,
    DEFAULT_MAX_REELS_PER_PROVIDER,
    get_settings,
)
from app.database import get_db
from app.services.ingestion import run_source_sync

logger = logging.getLogger("scheduler")

MAX_BACKOFF_SECONDS = 6 * 60 * 60  # cap at 6 hours
REFRESH_INTERVAL_SECONDS = 300  # pick up source-table edits every 5 min

# A sync that has been 'running' longer than this was cancelled, not slow.
# Generous enough to clear the slowest legitimate run (27 outlets, each
# with its own fetch timeout and media enrichment).
ORPHANED_SYNC_LOG_SECONDS = 3600

# Consecutive DNS failures before a source is disabled on THIS host.
# High enough that a transient resolver blip does not lose an outlet,
# low enough that a dead hostname stops costing a job every cycle.
DNS_FAILURE_DISABLE_THRESHOLD = 5

# source_id -> consecutive DNS failures on this host.
_consecutive_dns_failures: dict[str, int] = {}


def _saw_dns_failure(result: Any) -> bool:
    """True when a run failed specifically because a hostname did not resolve."""
    for detail in getattr(result, "error_details", []) or []:
        if detail.get("type") == "dns_failure":
            return True
    return False


async def _disable_source(db, source_id: str, source_row: dict) -> bool:
    """
    Switch a source off without deleting it.

    `enabled=false` leaves the row, its adapter config and its history
    intact, so re-enabling is a single UPDATE in the admin surface — and
    a source that is only unreachable from this one host can be turned
    back on the moment DNS changes.
    """
    # Imported here rather than at module scope: app.services.ingestion
    # imports this module for run_source_sync, so a module-level import
    # would be circular. Without it `db_call` is an unbound name and the
    # NameError lands in the except below — which means the breaker
    # silently disables nothing, forever.
    from app.services.ingestion import db_call

    try:
        await db_call(
            lambda: db.table("sources")
            .update({"enabled": False})
            .eq("id", source_id)
            .execute()
        )
        return True
    except Exception:
        logger.exception("Could not disable unresolvable source %s", source_id)
        return False


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
        # DNS failures are counted apart from ordinary ones. A source
        # whose hostname does not resolve from THIS host will keep
        # failing forever, and retrying it every backoff cycle for the
        # life of the process tells us nothing new.
        #
        # It is deliberately not disabled on the first failure: the same
        # outlet resolves fine from Render while failing on a laptop, and
        # vice versa. Only after several consecutive failures here is
        # it switched off, and the row stays in the table so re-enabling
        # is a single UPDATE.
        dns_failures = _consecutive_dns_failures.get(source_id, 0) + (
            1 if _saw_dns_failure(result) else 0
        )
        _consecutive_dns_failures[source_id] = dns_failures

        if dns_failures >= DNS_FAILURE_DISABLE_THRESHOLD:
            if await _disable_source(db, source_id, source_row):
                logger.warning(
                    "Source %s disabled after %s consecutive DNS failures. "
                    "Its hostname does not resolve from this host — re-enable "
                    "it if that is not expected.",
                    source_row.get("name"), dns_failures,
                )
            _consecutive_dns_failures.pop(source_id, None)
            _consecutive_failures.pop(source_id, None)
            _remove_jobs(source_id)
            return

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
        _consecutive_dns_failures.pop(source_id, None)


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
            _register_reels_job()
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
# REELS
#
# A pipeline of its own: short-form video from licensed providers
# rather than articles from newsrooms. It shares this scheduler and
# nothing else. A video provider being down, rate limited or simply
# unconfigured must never be able to affect a single outlet poll, so
# the reels job runs one pass over every provider and reports each
# provider separately.
# =============================================================

REELS_JOB_ID = "ingest-reels"

# Which configured search term the current reels pass is using. Advances
# one step per pass so a single term cannot monopolise the feed.
_reels_pass = 0

#: source_key -> monotonic timestamp of its last completed pass.
#:
#: Module-level for the same reason `_reels_pass` is: the scheduler is a
#: long-lived object and this has to outlive any single pass. Reset only
#: on restart, which is the correct semantics — a provider that was just
#: polled should not be polled again on the other side of a redeploy.
_reels_last_run: dict[str, float] = {}

# (source_key, the Settings field holding its comma-separated keys).
# A None field means the provider needs no key at all, which is what
# keeps /category/reels alive on a fresh deploy with nothing set.
#
# Wikimedia is listed unconditionally and is never filtered out below:
# a deployment with zero keys configured still serves a real page
# rather than an empty one.
REEL_PROVIDERS: tuple[tuple[str, str | None], ...] = (
    ("wikimedia", None),
    ("pexels", "pexels_api_keys"),
    ("pixabay", "pixabay_api_keys"),
    # An EMBEDDING source, not a redistributable one: the adapter embeds
    # YouTube's own player and never decodes a media stream. It is also
    # by far the most expensive key here — search.list is 100 units a call
    # against a 10,000/day default — so on the default 5-minute cadence
    # one key would be spent inside half an hour. The adapter declares
    # `min_poll_seconds = 3600` and the loop below honours it, so the key
    # works correctly as soon as it is set and this cadence can stay where
    # the operator wants it.
    ("youtube", "youtube_api_keys"),
)

# Wikimedia's API policy asks every client to identify itself, and
# anonymous traffic is what gets access withdrawn. Set this to something
# that reaches a human before going live — it is a contact address, not
# a version string. It is a Settings field (reels_user_agent) rather
# than a bare os.environ read so that setting REELS_USER_AGENT in .env
# works like every other setting here.


def _reels_health_row(
    source_key: str,
    *,
    enabled: bool,
    missing_env_var: str | None,
    key_count: int,
    min_poll_seconds: int = 0,
    last_run_at: str | None = None,
    last_count: int | None = None,
    last_error: str | None = None,
    last_status: str | None = None,
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "enabled": enabled,
        "missing_env_var": missing_env_var,
        "key_count": key_count,
        "min_poll_seconds": min_poll_seconds,
        "last_run_at": last_run_at,
        "last_count": last_count,
        "last_error": last_error,
        "last_status": last_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _record_reels_health(
    source_key: str,
    *,
    enabled: bool,
    missing_env_var: str | None,
    key_count: int,
    min_poll_seconds: int = 0,
    last_run_at: str | None = None,
    last_count: int | None = None,
    last_error: str | None = None,
    last_status: str | None = None,
) -> None:
    """
    Write one provider's state where the admin dashboard can read it.

    Never raises. A health row that cannot be written is a missing
    insight, not a failed reels pass, and the pass it describes has
    already happened. The reels tab must not stop working because the
    dashboard is down.
    """
    row = _reels_health_row(
        source_key,
        enabled=enabled,
        missing_env_var=missing_env_var,
        key_count=key_count,
        min_poll_seconds=min_poll_seconds,
        last_run_at=last_run_at,
        last_count=last_count,
        last_error=last_error,
        last_status=last_status,
    )

    try:
        await asyncio.to_thread(
            lambda: get_db()
            .table("reels_source_health")
            .upsert(row, on_conflict="source_key")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a missing insight is not a failure
        logger.debug(
            "Reels: could not record health for %s (%s). The table needs "
            "migration 0018.",
            source_key,
            exc,
        )


def _reels_summary(health: Any) -> str:
    """
    One log line for one provider, from its SourceHealth.

    'Returned nothing' and 'was never asked' must never read alike: a
    provider that answered with zero reels and a provider we had no
    key for are different problems with different fixes, and the log
    is the only place that distinction survives.
    """
    if health.error:
        return f"{health.source_key} FAILED: {health.error}"

    line = (
        f"{health.source_key} ok: {health.stored} stored of {health.fetched} "
        f"fetched, {health.skipped} skipped, {health.keys_used} key(s) used"
    )
    if health.notes:
        line = f"{line} ({'; '.join(health.notes)})"
    return line


def reels_per_provider_cap(settings: Any) -> int:
    """
    The most clips ONE provider may contribute to a single pass.

    `max_reels_per_provider` is the name; `reels_per_source` was the
    name before it was renamed to say what it bounds, and a settings
    object that still carries the old one is honoured. Never below
    one — a cap of zero would fetch nothing, which is a different
    setting (turn the feature off) wearing the same clothes.
    """
    raw = getattr(settings, "max_reels_per_provider", None)
    if raw is None:
        raw = getattr(settings, "reels_per_source", None)
    if raw is None:
        raw = DEFAULT_MAX_REELS_PER_PROVIDER
    try:
        return max(int(raw or 0), 1)
    except (TypeError, ValueError):
        return 1


def reels_cycle_cap(settings: Any) -> int:
    """
    The most clips ONE whole reels pass may fetch, across every
    provider. Never below one.

    A settings object that does not carry the field falls back to the
    same default the Settings class declares, not to one — a cap of
    one would starve every provider but the first.
    """
    try:
        return max(
            int(
                getattr(
                    settings,
                    "max_reels_per_cycle",
                    DEFAULT_MAX_REELS_PER_CYCLE,
                )
                or 0
            ),
            1,
        )
    except (TypeError, ValueError):
        return 1


async def _sync_reels() -> None:
    """
    One reels pass, hard-bounded.

    APScheduler runs this on `max_instances=1`. A pass that never
    RETURNS therefore never runs again — and a provider that accepts
    the connection then hangs holds the slot for the life of the
    process. That is not hypothetical: reels stopped ingesting for 23
    hours while this endpoint still reported healthy, with every
    exception swallowed by the body's own handler.

    A timeout converts the only failure that is otherwise permanent
    into an ordinary one. The next pass runs exactly as it would have
    if this one had thrown.
    """
    settings = get_settings()
    budget = max(int(settings.reels_pass_timeout_seconds or 0), 30)

    try:
        await asyncio.wait_for(_sync_reels_body(), timeout=budget)
    except asyncio.TimeoutError:
        # Deliberately NOT re-raised as CancelledError — wait_for
        # cancels the inner task, and the pass is abandoned, not the
        # process. The scheduler must keep running.
        logger.warning(
            "Reels: pass exceeded %ss and was abandoned; the next pass "
            "will run normally.", budget,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Reels: pass aborted; the scheduler keeps running.")


async def _sync_reels_body() -> None:
    """
    One reels pass: every provider once, reported one by one.

    Providers run sequentially rather than concurrently. Each is one
    small metered request sequence, they do not overlap for any useful
    amount of time, and serialising keeps the rate-limit story simple:
    a provider that cools a key down cannot be racing the next one.

    The whole body is wrapped because this job lives inside a scheduler
    that also runs the outlets. An exception escaping here is an
    unhandled APScheduler job error, and one bad video provider must
    not be able to take the news pipeline down with it.
    """
    # Imported here rather than at module scope, for the same reason
    # `db_call` is: the reels package is optional, and importing it at
    # module load would make an optional feature a hard start-up
    # dependency for the whole scheduler.
    from app.services.reels.keys import build_pool
    from app.services.reels.providers import build_source
    from app.services.reels.store import ingest_reels

    settings = get_settings()

    try:
        # The two reels caps, read HERE — where the providers are
        # actually asked — rather than being numbers in a config file
        # that nothing consults.
        #
        # `per_provider_cap` bounds one provider's contribution to one
        # pass. `cycle_cap` bounds the whole pass, across every
        # provider: one pass over every provider IS one cycle on the
        # video side, the way a window of time is one cycle on the
        # article side.
        per_provider_cap = reels_per_provider_cap(settings)
        cycle_cap = reels_cycle_cap(settings)
        terms = [
            t.strip()
            for t in (settings.reels_search_terms or "").replace("\n", ",").split(",")
            if t.strip()
        ]

        # Every configured term, comma-joined, rather than one per pass.
        #
        # This used to rotate a single term per pass, so with twelve
        # terms a full cycle took an hour — and each pass REPLACED what
        # the feed showed rather than adding to it. The category page
        # therefore showed whatever the last pass happened to find, and
        # the reader saw the same handful of clips over and over.
        #
        # Passing the whole list lets a provider search each term in one
        # pass. WikimediaSource scopes each one with `intitle:` and
        # unions the results, so the feed accumulates every term's
        # material in one go instead of one term's per hour.
        #
        # The rotation counter is kept, because a provider that takes a
        # single hint (Pexels, Pixabay) still benefits from seeing a
        # different term each pass rather than the same one for ever.
        hint = ""
        if terms:
            # `global` is required, not decorative: the += below makes
            # this name local to the function for the whole of it, so the
            # read above would raise UnboundLocalError without it.
            global _reels_pass
            rotated = terms[_reels_pass % len(terms)]
            _reels_pass += 1
            # The rotated term first, so a provider that reads only the
            # first one still rotates; the rest follow for the providers
            # that can use them all.
            hint = ",".join([rotated] + [t for t in terms if t != rotated])

        # Pools are built — and cleared — once per pass, before any
        # provider runs. Resetting here is what lets a quota that
        # returns early be picked up without a redeploy, and what stops
        # one mistyped key from sitting in cooldown for the life of the
        # process after it has been corrected.
        pools: list[tuple[str, Any]] = []
        for source_key, keys_field in REEL_PROVIDERS:
            raw = getattr(settings, keys_field) if keys_field else None
            pool = build_pool(source_key, raw)

            if keys_field and pool.is_empty:
                # Not a failure. Nothing is misconfigured — we simply
                # have no credential for this provider, and the others
                # are still running.
                logger.info(
                    "Reels: %s SKIPPED, no API key configured.", source_key
                )
                # But the OPERATOR has to be able to see it, and a log
                # line at INFO is not a way to see it. This is the row
                # the admin dashboard reads, and it names the variable
                # to set. Without it the Reels tab looks broken for a
                # reason nobody can find.
                await _record_reels_health(
                    source_key,
                    enabled=False,
                    missing_env_var=(keys_field or "").upper(),
                    key_count=0,
                )
                continue

            pool.reset_cooldowns()
            pools.append((source_key, pool))

            await _record_reels_health(
                source_key,
                enabled=True,
                missing_env_var=None,
                key_count=len(pool),
            )

        # One client for the whole pass. A provider per client would mean
        # a new connection pool per source for no benefit — the requests
        # are serial anyway, and httpx closes the pool when this exits.
        import httpx

        async with httpx.AsyncClient(
            timeout=settings.reels_http_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.reels_user_agent},
        ) as client:
            # What this whole pass has fetched so far. The pass's total
            # cap is spent against it, so the cap counts what was
            # actually FETCHED rather than what happened to be asked
            # for.
            total_fetched = 0

            for index, (source_key, pool) in enumerate(pools):
                try:
                    # build_source raises KeyError for an unknown key and
                    # returns a ReelSource otherwise — it never returns
                    # None. The adapters take a single search hint rather
                    # than a list, so the first configured term wins and
                    # the rest are a documented no-op; a provider that
                    # wants breadth spreads its own query internally.
                    source = build_source(
                        source_key,
                        pool=pool,
                        client=client,
                        hint=hint,
                    )

                    # Respect the source's own cost, if it declared one.
                    #
                    # Some APIs are priced in quota units and one poll
                    # can cost a hundred of them (YouTube's search.list is
                    # the case in this codebase). Polling those on the
                    # same cadence as the free ones does not fail loudly
                    # — it spends the day's quota in half an hour and
                    # then quietly returns nothing for the other 23.5,
                    # while every health report still says the source is
                    # fine. The provider knows its own price, so it
                    # declares it and this enforces it.
                    #
                    # The skip is recorded as info, not warning: it is
                    # the system working, and a warning here every pass
                    # would train the operator to ignore this line.
                    min_interval = getattr(source, "min_poll_seconds", 0)
                    if min_interval:
                        last = _reels_last_run.get(source_key)
                        elapsed = time.monotonic() - last if last else None
                        if elapsed is not None and elapsed < min_interval:
                            logger.info(
                                "Reels: %s SKIPPED, %.0fmin of its %.0fmin "
                                "interval not yet elapsed.",
                                source_key,
                                elapsed / 60,
                                min_interval / 60,
                            )
                            continue

                    # ---- The budget for this provider ----
                    #
                    # Two bounds, both real:
                    #
                    #   per_provider_cap — how many clips THIS provider
                    #     may contribute to the pass.
                    #   cycle_cap — how many clips the WHOLE pass may
                    #     fetch, across every provider.
                    #
                    # What is left of the pass's budget is shared
                    # fairly between the providers still to run, so a
                    # tight budget thins every provider's share rather
                    # than starving the last one to be asked, which is
                    # what spending it first-come-first-served does.
                    #
                    # The share is a ceiling division, so it is never
                    # larger than what is actually left, and a provider
                    # is skipped outright once the budget is spent —
                    # which is what keeps the pass's total inside the
                    # cap rather than merely near it.
                    remaining = max(cycle_cap - total_fetched, 0)
                    providers_left = max(len(pools) - index, 1)

                    if remaining <= 0:
                        logger.warning(
                            "Reels: %s SKIPPED, this pass's budget of %d "
                            "clip(s) is spent.",
                            source_key, cycle_cap,
                        )
                        continue

                    share = -(-remaining // providers_left)
                    limit = min(per_provider_cap, share)

                    if limit < per_provider_cap:
                        logger.warning(
                            "Reels: %s was given %d of the %d clip(s) left "
                            "in this pass (per-provider cap %d, pass cap %d).",
                            source_key, limit, remaining,
                            per_provider_cap, cycle_cap,
                        )

                    # ingest_reels is written around a whole pass, so one
                    # source at a time still goes in as a one-entry map
                    # and comes back as a one-entry list. That keeps the
                    # concurrency, isolation and health-reporting logic in
                    # exactly one place instead of twice.
                    results = await ingest_reels(
                        get_db(),
                        sources={source_key: source},
                        per_source=limit,
                    )
                    health = results[0]
                    total_fetched += int(getattr(health, "fetched", 0) or 0)
                    _reels_last_run[source_key] = time.monotonic()

                    # Record what this pass actually produced, so the
                    # dashboard can say "this provider ran and returned
                    # 30 clips" rather than only "this provider is on".
                    await _record_reels_health(
                        source_key,
                        enabled=True,
                        missing_env_var=None,
                        key_count=len(pool),
                        min_poll_seconds=int(
                            getattr(source, "min_poll_seconds", 0) or 0
                        ),
                        last_run_at=datetime.now(timezone.utc).isoformat(),
                        last_count=int(getattr(health, "stored", 0) or 0),
                        last_error=(health.error or None),
                        last_status="error" if health.error else "ok",
                    )
                except asyncio.CancelledError:
                    # Teardown, not a provider failure. See _sync_one: a
                    # swallowed cancellation breaks structured concurrency.
                    raise
                except Exception as exc:
                    # One provider's adapter blowing up must not end the
                    # pass; the remaining providers are unaffected by it.
                    logger.exception("Reels: %s raised during this pass.", source_key)
                    await _record_reels_health(
                        source_key,
                        enabled=True,
                        missing_env_var=None,
                        key_count=len(pool),
                        last_run_at=datetime.now(timezone.utc).isoformat(),
                        last_error=f"{type(exc).__name__}: {exc}"[:300],
                        last_status="error",
                    )
                    continue

                if health.error:
                    logger.warning("Reels: %s", _reels_summary(health))
                else:
                    logger.info("Reels: %s", _reels_summary(health))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Reels: pass aborted; the scheduler keeps running.")


def _register_reels_job() -> None:
    """
    Make the reels job exist, or not exist, according to the setting.

    Called at startup and again on every registry refresh, so enabling
    or disabling the category does not need a redeploy to take effect.
    Disabling explicitly REMOVES the job rather than simply declining
    to add it: a process that was started with reels on must not keep
    polling a provider the operator has since turned off.
    """
    settings = get_settings()
    scheduler = get_scheduler()

    existing = None
    try:
        existing = scheduler.get_job(REELS_JOB_ID)
    except Exception:
        logger.debug("Could not read the reels job; assuming it is absent")

    if not settings.enable_reels:
        if existing:
            try:
                existing.remove()
                logger.info("Scheduler: reels job removed (enable_reels is off).")
            except Exception:
                logger.exception("Could not remove the reels job")
        return

    interval = max(int(settings.reels_poll_seconds or 0), MIN_POLL_SECONDS)

    try:
        scheduler.add_job(
            _sync_reels,
            trigger=IntervalTrigger(seconds=interval),
            id=REELS_JOB_ID,
            replace_existing=True,   # pick up a changed interval
            max_instances=1,        # never two video passes at once
            coalesce=True,          # collapse missed runs into one
            misfire_grace_time=120,
            next_run_time=datetime.now(timezone.utc)
            + timedelta(seconds=_stagger(REELS_JOB_ID)),
        )
    except Exception:
        # The outlets are already scheduled; failing to add an optional
        # job is a warning, not a reason to refuse to start.
        logger.exception("Scheduler: could not schedule the reels job")


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

    # Optional and keyed separately from the outlets: a project that
    # never enables reels must not gain a job because of it.
    _register_reels_job()

    return scheduled > 0


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.debug("Scheduler shutdown raised; ignoring")