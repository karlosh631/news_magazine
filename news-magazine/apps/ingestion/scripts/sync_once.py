"""
Run exactly one full sync pass and exit.

Used by the scheduled GitHub Actions workflow, which cannot run a
long-lived daemon. Shares the pipeline with worker.py — same adapters,
same dedup, same media extraction, same TTL prune — so what runs in CI
is what runs in production.

Exits non-zero only when every source failed, which almost always means
bad credentials rather than a flaky network. One dead outlet must not
turn the whole job red.

REELS
-----
The reels pass runs here too, and that is a correction rather than a
feature. It used to run ONLY inside the long-lived worker's scheduler,
which meant the two paths had different failure modes: if the worker
was asleep, restarting, or simply down, the reels category stopped
updating entirely while the news feed kept running on the cron. The
user saw exactly that — news minutes old, reels frozen for a day — and
the cause was not a bug in the reels pipeline at all but the reels
pipeline simply not being in the unattended path.

Running it here makes reels fetch on the same five-minute cron as
news, so the category keeps moving even when the worker is down. The
worker still runs its own pass, and the two are safe to overlap: the
store upserts on (source_key, external_id).

THE ALMANAC RUNS HERE TOO
-------------------------
For the same reason and one more time. The panchang and the twelve
horoscopes were fetched only by the long-lived worker, so they
depended on a service that sleeps — and the Rashifal page said
"Today's almanac has not been fetched yet" on the days the worker
was suspended. The pass is here now as well, and it asks the
database first, so on most runs it costs one query and no requests.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

INGESTION_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INGESTION_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")

from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.services.ingestion import db_call, run_source_sync  # noqa: E402

# Run sources in batches so the workflow does not open 25 sockets at once
# against 25 different publishers simultaneously.
CONCURRENCY = 5


def source_interval(source: dict, floor_seconds: int = 60) -> int:
    """
    How long this outlet must be left alone between two polls.

    The outlet's own row is the truth: `poll_interval_seconds` is what
    the operator agreed with that publisher, and it is the same number
    the long-lived worker uses for its own scheduler. The floor is
    `MIN_POLL_SECONDS` (60 by default), so a row that says zero still
    cannot turn the sweep into a load test.
    """
    try:
        interval = int(source.get("poll_interval_seconds") or 0)
    except (TypeError, ValueError):
        interval = 0
    return max(interval, floor_seconds)


def source_is_due(source: dict, now: datetime, floor_seconds: int = 60) -> bool:
    """
    Is this outlet due to be polled again?

    `last_success_at` is written by every poller - the worker's
    scheduler and this sweep alike - the moment a poll of that outlet
    finishes without failing. So it answers the only question that
    matters to a publisher: has this outlet been asked recently enough
    that asking again would be one request too many?

    An outlet that has never been polled is always due. An outlet whose
    last poll FAILED keeps its old timestamp (the store path leaves it
    alone on failure), so a broken outlet is retried at the next pass
    rather than being left to its own interval.
    """
    last = source.get("last_success_at")
    if not last:
        return True

    if isinstance(last, str):
        try:
            last_at = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return True
    elif isinstance(last, datetime):
        last_at = last
    else:
        return True

    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    elapsed = (now - last_at).total_seconds()
    return elapsed >= source_interval(source, floor_seconds)


async def main() -> int:
    settings = get_settings()
    db = get_db()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    now = datetime.now(timezone.utc)

    try:
        response = await db_call(
            lambda: db.table("sources")
            .select("*")
            .eq("enabled", True)
            .eq("allowed", True)
            .order("name")
            .execute()
        )
        sources = response.data or []
    except Exception as exc:  # noqa: BLE001
        print(f"::error::Could not read the sources table: {exc}")
        print("SOURCES_OK=0")
        return 2

    # =============================================================
    # THE CHEAP PASSES GO FIRST
    # =============================================================
    #
    # The reels, the almanac and the storage guard used to run AFTER
    # the news sources. The news sources take the whole window - they
    # read a couple of pages of every feed, and every story nobody has
    # seen before costs a page read and an image proof - and so the
    # window ran out with the news half done and the other three
    # passes never started at all.
    #
    # The symptom was a Reels page that said "245 clips in the
    # catalogue, 0 clips fetched today" and a Rashifal page that
    # sometimes showed yesterday's horoscope: the passes were in the
    # code, and they were never reached.
    #
    # They are quick and they are bounded, so they go first. Whatever
    # happens to the news pass after them, the reels are fetched, the
    # almanac is fetched and the database is pruned.
    #
    # ---- Reels ----
    #
    # A separate concern from the news sources, and deliberately
    # non-fatal: a reels failure must not turn a green news run red,
    # and a green news run must not be reported as if reels had also
    # succeeded.
    reels_ok = await sync_reels_once()

    # ---- The almanac ----
    #
    # The same correction, for the same reason, one more time.
    #
    # The panchang was fetched ONLY by the long-lived worker's
    # scheduler. The GitHub Actions cron — the path that is
    # guaranteed to run, because GitHub does not suspend free accounts
    # the way Render suspends a free instance — never fetched it. So
    # the almanac depended on a service that sleeps, and when it
    # slept the Rashifal page said "Today's almanac has not been
    # fetched yet" for no reason the reader could understand.
    #
    # The pass is now in the cron too. It asks the database whether
    # today's row is there and fresh, so a five-minute cron costs one
    # query and no requests on most runs.
    panchang_ok = await sync_panchang_once()

    # ---- The storage guard ----
    #
    # Once per pass, and the pass runs every three minutes, so the
    # guard runs every three minutes without anybody watching. The
    # log tables go by age, and if the database is at eighty percent
    # of its limit the oldest half of the articles goes as well. The
    # site keeps serving either way.
    await storage_guard_once(db, settings)

    # THE SWEEP DOES NOT ASK EVERY OUTLET EVERY TIME
    # ----------------------------------------------
    # This pass runs every three minutes now, and every pass used to
    # ask every outlet - so an outlet that agreed to be polled every
    # five minutes was being asked every three. The interval in the
    # outlet's own row is the number that was agreed with that
    # publisher, so this pass honours it: an outlet is polled when its
    # interval has elapsed and left alone until then.
    #
    # That is what makes a three-minute sweep safe. The outlets that
    # answer stay on a three-minute rhythm; the five that refuse us
    # keep the slower rhythm they were given (and Ekantipur, whose poll
    # costs a page fetch and three image probes PER CARD, keeps the
    # slowest of all).
    due: list[dict] = []
    not_due: list[dict] = []
    for source in sources:
        if source_is_due(source, now, settings.min_poll_seconds):
            due.append(source)
        else:
            not_due.append(source)

    if not_due:
        print(
            f"{len(not_due)} outlet(s) are not due yet and will not be asked: "
            + ", ".join(
                f"{s.get('name', '?')} (every "
                f"{source_interval(s, settings.min_poll_seconds)}s)"
                for s in not_due
            )
            + "\n"
        )

    sources = due

    if not sources:
        if not_due:
            # Every outlet was inside its own interval. That is the
            # sweep working, not a failure: a run that turned RED here
            # would make the workflow report "no source was
            # successfully synced" and teach the operator to ignore it.
            print(
                "Nothing was due this pass - every outlet is inside its own "
                "interval. The next pass will pick them up."
            )
            print("SOURCES_OK=1")
            return 0
        print(
            "No enabled+allowed sources. Apply migration "
            "0008_seed_nepali_sources.sql first."
        )
        print("SOURCES_OK=0")
        return 1

    print(
        f"Syncing {len(sources)} source(s) at "
        f"{datetime.now(timezone.utc).isoformat()} "
        f"(concurrency {CONCURRENCY})\n"
    )

    async def sync_one(source: dict) -> tuple[str, str, object]:
        async with semaphore:
            try:
                result = await run_source_sync(source)
                return (source.get("name", "?"), "ok", result)
            except Exception as exc:  # noqa: BLE001
                return (source.get("name", "?"), f"error: {exc}", None)

    outcomes = await asyncio.gather(*(sync_one(s) for s in sources))

    ok = failed = 0
    totals = {"fetched": 0, "new": 0, "duplicates": 0, "rejected": 0}

    print(f"{'SOURCE':30s} {'FETCH':>6s} {'NEW':>5s} {'DUP':>5s} {'REJ':>5s}  STATUS")
    print("-" * 88)

    for name, status, result in outcomes:
        if status == "ok":
            data = result.as_dict()  # type: ignore[union-attr]
            for key in totals:
                totals[key] += data.get(key, 0) or 0

            # run_source_sync never raises: a failed source comes back as
            # a result carrying errors. Treating that as ok made a fully
            # broken run report 25/25 green.
            if data.get("errors") and not data.get("new"):
                failed += 1
                print(
                    f"{name[:29]:30s} {data['fetched']:6d} {data['new']:5d} "
                    f"{data['duplicates']:5d} {data['rejected']:5d}  FAILED"
                )
                continue

            ok += 1
            label = "partial" if data.get("errors") else "ok"
            if data.get("capped"):
                # A fetch cap truncated this outlet's poll. The run is
                # still a success, but stories were left behind, and a
                # green table that is quietly throwing articles away is
                # the thing this line exists to prevent.
                label = f"{label}, capped"
            print(
                f"{name[:29]:30s} {data['fetched']:6d} {data['new']:5d} "
                f"{data['duplicates']:5d} {data['rejected']:5d}  {label}"
            )
        else:
            failed += 1
            print(f"{name[:29]:30s} {'':6s} {'':5s} {'':5s} {'':5s}  {status[:24]}")

    print("-" * 88)
    print(
        f"{ok}/{len(sources)} sources ok · {totals['fetched']} fetched · "
        f"{totals['new']} new · {totals['duplicates']} deduplicated · "
        f"{totals['rejected']} rejected"
    )
    print(f"SOURCES_OK={ok}")

    return 0 if ok > 0 else 3


async def storage_guard_once(db, settings) -> None:
    """Run the storage guard, and say what it did. Never raises."""
    try:
        from app.services.storage_guard import run_storage_guard

        report = await run_storage_guard(db, settings)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::The storage guard could not run: {exc}")
        return

    used = report.get("used_bytes") or 0
    limit = report.get("limit_bytes") or 1
    print(
        f"\nStorage: {used / 1024 / 1024:.1f} MB of {limit / 1024 / 1024:.0f} MB "
        f"({(used / limit) * 100:.1f}%) · "
        f"{report.get('logs_pruned', 0)} sync log(s), "
        f"{report.get('errors_pruned', 0)} error(s) and "
        f"{report.get('analytics_pruned', 0)} analytics row(s) pruned by age"
    )
    if report.get("emergency_pruned"):
        print(
            f"::warning::The database was near its limit, so "
            f"{report['emergency_pruned']} old article(s) were deleted to keep "
            f"the site running."
        )
    print("STORAGE_OK=1")


async def sync_panchang_once() -> bool:
    """
    One almanac pass — today's panchang, today's horoscopes and the
    week ahead of both.

    Returns True when the pass ran, False when it did not. It never
    raises: the almanac is a missing line in a header, and a missing
    line must not take the news sync down with it.
    """
    try:
        from app.services.panchang import sync_panchang

        if not get_settings().enable_panchang:
            print("\nThe almanac is switched off (ENABLE_PANCHANG=false).\n")
            print("PANCHANG_OK=1")
            return True

        print("\nSyncing the almanac...\n")
        panchang = await sync_panchang(get_db())
        if panchang is not None:
            print(
                f"  stored {panchang.bs_label} — {panchang.tithi}, "
                f"{panchang.nakshatra}"
            )
        else:
            print("  already fresh; nothing to do.")
        print("PANCHANG_OK=1")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Almanac pass failed: {exc}")
        print("PANCHANG_OK=0")
        return False


async def sync_reels_once() -> bool:
    """
    One reels pass, using the same code the worker's scheduler runs.

    Reuses `_sync_reels` rather than reimplementing it, so the two
    paths cannot drift: the timeout that stops a hung provider from
    holding the slot, the per-provider poll intervals, the key pools
    and the health reporting are all the same.

    Returns True when the pass ran, False when it did not. It never
    raises: the reels category is optional, and a broken reels pass
    must not take the news sync down with it.
    """
    try:
        from app.workers.scheduler import _sync_reels

        print("\nSyncing reels...\n")
        await _sync_reels()
        print("REELS_OK=1")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Reels pass failed: {exc}")
        print("REELS_OK=0")
        return False


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))