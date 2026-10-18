"""
Run exactly one full sync pass and exit.

Used by the scheduled GitHub Actions workflow, which cannot run a
long-lived daemon. Shares the pipeline with worker.py — same adapters,
same dedup, same media extraction, same TTL prune — so what runs in CI
is what runs in production.

Exits non-zero only when every source failed, which almost always means
bad credentials rather than a flaky network. One dead outlet must not
turn the whole job red.
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


async def main() -> int:
    settings = get_settings()
    db = get_db()
    semaphore = asyncio.Semaphore(CONCURRENCY)

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

    if not sources:
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))