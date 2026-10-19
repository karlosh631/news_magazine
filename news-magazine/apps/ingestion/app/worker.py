"""
Standalone ingestion daemon.

    python -m app.worker

Runs continuously and forever, independent of the HTTP service. This
is the intended production entrypoint, and it exists because Stage 1's
scheduler only ever lived inside the FastAPI process:

    - `uvicorn app.main:app --workers 4` started FOUR schedulers, each
      polling the same 27 outlets, quadrupling traffic to every
      publisher and quadrupling the duplicate-insert race.
    - `uvicorn --reload` did the same on every file save.
    - There was no way to run ingestion without also running an HTTP
      server, so scaling the API scaled the scraper.

worker.py owns exactly one schedule. Run it as its own service, and run
main.py with WORKER_MODE=true (or just leave SCHEDULER_ENABLED=true for
single-process local development).

Behaviour:
    - Boots, schedules every enabled+allowed source on its own interval.
    - Re-reads the sources table every 5 minutes so admin edits take
      effect without a restart.
    - Exponential backoff per source, pausing its interval job.
    - Staggered first runs so 27 outlets are not hit in the same second.
    - Retries the boot poll if the database is not reachable yet, rather
      than exiting or silently idling.
    - Graceful shutdown on SIGINT/SIGTERM.

Never exits on a per-source error. Only a repeated failure to reach the
database at all will restart the loop with backoff, and even then it
keeps retrying rather than giving up.

Why it also serves HTTP
------------------------
Render's Background Workers start at $7/month. On the free tier the
service has to be a Web Service, and a Web Service that binds no port
fails Render's health check and is SIGTERMed roughly every 15 minutes:

    worker.signal   signal=SIGTERM
    worker.stopped
    ==> Timed Out

So when `PORT` is present, this also runs the real FastAPI app on that
port. Not a dummy "OK" responder — the actual application, so
`/api/health` genuinely probes the database and `/api/scrape-on-demand`,
`/api/search` and `/api/prune` become reachable, which is what the web
app's search-resurrection path calls.

`mark_worker_mode()` is called first so the app's own lifespan does not
start a SECOND scheduler. One process, one schedule, one port.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading

import structlog

from app.config import get_settings
from app.workers import scheduler as scheduler_module

log = logging.getLogger("worker")

# ---------------------------------------------------------
# Structured logging
#
# json for anything that ships to a log aggregator, console for local
# development. Stage 1 emitted bare logging.basicConfig strings, which
# are unusable once you have more than one worker process.
# ---------------------------------------------------------


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (httpx, apscheduler, supabase) through the
    # same renderer so one log line format covers everything.
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = structlog.get_logger("worker")


async def _boot(scheduler_module_ref) -> bool:
    """
    Start the scheduler and confirm it actually scheduled something.

    Returns True when at least one source is active. Retries rather than
    exiting when the database is not reachable yet — a worker that dies
    because Postgres took 30s to come up is worse than one that waits.
    """
    delay = 5

    for attempt in range(1, 13):  # ~5 minutes of retries
        try:
            scheduled = await scheduler_module.start()
            if scheduled:
                log.info(
                    "worker.ready",
                    active_sources=scheduled,
                    retention_hours=get_settings().retention_hours,
                    dedup_window_hours=get_settings().dedup_window_hours,
                )
                return True

            log.warning(
                "worker.no_sources",
                hint=(
                    "Apply supabase/migrations/0008_seed_nepali_sources.sql, "
                    "then set enabled=true and allowed=true."
                ),
            )
            # Nothing to do, but do not exit — an operator may enable a
            # source while we are waiting.
            return True

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "worker.boot_retry",
                attempt=attempt,
                delay_seconds=delay,
                error=str(exc)[:200],
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    log.error("worker.boot_failed", hint="Giving up after 12 retries.")
    return False


async def main() -> int:
    configure_logging()

    settings = get_settings()
    log.info(
        "worker.starting",
        python=sys.version.split()[0],
        retention_hours=settings.retention_hours,
        dedup_window_hours=settings.dedup_window_hours,
        fuzzy_threshold=settings.fuzzy_title_threshold,
        enrich_media=settings.enrich_media,
        gemini_summary=settings.enable_gemini_summary,
        gemini_ranking=settings.enable_gemini_ranking,
    )

    stop = asyncio.Event()

    loop = asyncio.get_running_loop()

    def request_stop(signame: str) -> None:
        log.info("worker.signal", signal=signame)
        stop.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame), request_stop, signame
            )
        except (NotImplementedError, AttributeError):
            # Windows has no add_signal_handler for SIGTERM; fall back to
            # KeyboardInterrupt, which the outer handler catches.
            pass

    if not await _boot(scheduler_module):
        return 1

    # Serve HTTP only once ingestion is confirmed running, so a port
    # problem can never delay or block the scheduler's first poll.
    start_http_server()

    log.info("worker.running", hint="Ctrl+C to stop.")

    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler_module.shutdown()
        log.info("worker.stopped")

    return 0


# ---------------------------------------------------------
# HTTP
#
# Render's free tier has no Background Worker, so the service must
# present a port or be killed. Serving the real application — rather
# than a stub that answers 200 to everything — means the health check
# is honest and the endpoints the web app already calls become usable.
# ---------------------------------------------------------


def start_http_server() -> threading.Thread | None:
    """
    Serve app.main:app on $PORT in a background thread.

    No-ops when PORT is unset, which is every local run — a developer
    running `python -m app.worker` should not suddenly have a web
    server on their machine.

    Returns the thread, or None if the port was unusable. A failure to
    bind is never fatal: on a host that does not require a port (a real
    Background Worker, a VM, Docker with its own orchestrator) ingestion
    must keep running regardless.
    """
    port = os.environ.get("PORT")
    if not port:
        return None

    try:
        port_number = int(port)
    except ValueError:
        log.warning("PORT=%r is not a number; not serving HTTP.", port)
        return None

    def serve() -> None:
        import uvicorn

        from app import main as main_module

        # Must be set before uvicorn starts the app: main.py's lifespan
        # checks it and skips starting its own scheduler. Without this
        # the process would run TWO schedulers over the same 27
        # outlets, which is the exact bug this module was split out to
        # prevent.
        main_module.mark_worker_mode()

        try:
            uvicorn.run(
                main_module.app,
                host="0.0.0.0",
                port=port_number,
                log_level="warning",
                # uvicorn.Server.run() builds its own loop, so it must
                # not be given this process's running one.
                loop="asyncio",
            )
        except Exception:
            # Losing the health endpoint is bad; losing ingestion is
            # worse. Never let the HTTP side take the worker down.
            log.exception(
                "HTTP server on port %s stopped; ingestion continues", port_number
            )

    thread = threading.Thread(target=serve, name="http", daemon=True)
    thread.start()
    log.info("worker.http", port=port_number)
    return thread


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)