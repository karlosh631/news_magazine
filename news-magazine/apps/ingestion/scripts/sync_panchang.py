"""
Fetch the almanac and the horoscopes, and exit.

THE DAILY MORNING FETCH
------------------------
The Nepali almanac and the twelve horoscopes are published every
morning and do not change during the day. So they are fetched once a
day, at one in the morning in Nepal, and stored.

Why one in the morning, and not "every time the worker happens to be
awake": the worker is a service that sleeps. Between the cron's runs
it can be suspended for hours, and a worker that is not running cannot
fetch anything. The news pipeline does not care — it catches up when
it wakes — but the almanac is a once-a-day publication, and a day that
is missed is a day the reader cannot have.

So there is a job for it, at a fixed time, that does not depend on the
worker being awake. The time is one in the morning NEPALI time, which
is 19:15 UTC the day before, because Kathmandu is UTC+05:45 and the
difference is not a round number of hours.

The worker still fetches it too, hourly. The two are safe to overlap:
the store is an upsert on the Gregorian day, so the second run
refreshes the row instead of adding a second one.

WHAT IT DOES NOT DO
-------------------
It does not touch the articles, the reels, the sources or anything
else. It writes two tables and nothing else, and it never raises: a
failed almanac is a missing line in a header, not a reason to fail a
scheduled job.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

INGESTION_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INGESTION_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")

from app.database import get_db  # noqa: E402


async def main() -> int:
    from app.config import get_settings
    from app.services.panchang import sync_panchang

    if not get_settings().enable_panchang:
        print("The almanac is switched off (ENABLE_PANCHANG=false).")
        print("PANCHANG_OK=1")
        return 0

    print("Fetching the almanac and the horoscopes...")

    try:
        panchang = await sync_panchang(get_db())
    except Exception as exc:  # noqa: BLE001 - a missing almanac is not a crash
        print(f"::warning::The almanac pass failed: {exc}")
        print("PANCHANG_OK=0")
        return 0

    if panchang is None:
        print("  already fresh; nothing to do.")
    else:
        print(
            f"  stored {panchang.bs_label} — {panchang.tithi}, "
            f"{panchang.nakshatra}, sunrise {panchang.sunrise:%H:%M}, "
            f"sunset {panchang.sunset:%H:%M}"
        )

    print("PANCHANG_OK=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
