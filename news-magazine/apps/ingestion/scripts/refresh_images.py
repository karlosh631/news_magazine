"""
Re-resolve the artwork of every row the marker has not visited.

WHY THIS IS A SEPARATE SCRIPT AND NOT JUST THE REPAIR PASS
-----------------------------------------------------------
The repair pass inside the poll does the same work, five rows a poll
per source. That is the right shape for a steady state: a row is
fetched exactly once, ever, and the marker stops it being fetched
again.

It is the wrong shape for a BACKLOG. There were 559 rows whose
picture had never been confirmed against their own page, because an
ingestion build older than the fix had been writing them for days
without the marker. At five a poll per source, the biggest
contributors - the ones with two hundred rows each - would take days
to clear, and every one of those rows is a card on the site with no
picture on it.

So this script walks the backlog directly, newest first, across every
source, and it is meant to be run once (and again whenever a new
backlog appears). It is dispatched from GitHub Actions so it runs
without anybody watching.

WHAT IT DOES
------------
For each row whose `image_verified_at` is null:

  - the article's OWN page is fetched, and the pictures on it are
    resolved;
  - if a picture is proven, the row gets that picture and the marker.
    The picture is then KNOWN to belong to the story, and the site
    will show it;
  - if no picture can be proven, the row is left unverified and the
    site shows the story without one. The stored picture is NOT
    deleted here: the site already refuses to show an unconfirmed
    picture, and keeping it lets a later pass try again with the
    article's page in a better mood.

WHAT IT NEVER DOES
------------------
It never invents a picture, never uses a feed thumbnail, and never
accepts a picture that was not found on the story's own page. A
picture that belongs to another story is worse than no picture, and
the whole point of the marker is that the site can tell the two
apart.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

INGESTION_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INGESTION_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.services.ingestion import db_call  # noqa: E402
from app.sources.media import (  # noqa: E402
    ImageValidator,
    candidate_images,
    extract_from_article,
    media_client,
    resolve_live_image,
)

#: How many picture candidates are probed per row. The same number the
#: poll's own store path uses, so a repaired row is held to exactly
#: the standard a freshly stored one is.
PROBE_CANDIDATES = 3

#: A pause between rows.
#:
#: One page fetch per row, one request per second at the slow end.
#: The rows are spread over a few dozen publishers, and a publisher
#: being asked for eight hundred pages in ten minutes is a publisher
#: that starts refusing — which is exactly how the five outlets that
#: are being refused with 403 got that way.
DELAY_SECONDS = 1.0

#: How many rows one run will touch. A GitHub Actions job has 10
#: minutes, and a page fetch plus its probes is a second or two, so
#: 250 is a comfortable ceiling for one run.
DEFAULT_LIMIT = 250


async def run(limit: int, delay: float, dry_run: bool) -> int:
    settings = get_settings()
    db = get_db()

    try:
        response = await db_call(
            lambda: db.table("articles")
            .select(
                "id, headline, slug, source_article_url, canonical_url, "
                "featured_image_url, image_highres_url, image_verified_at, "
                "source_name_snapshot, published_at"
            )
            .eq("status", "published")
            .is_("duplicate_of", None)
            .is_("image_verified_at", None)
            # `not_` is a PROPERTY in this client, not a method: it
            # marks the NEXT filter as negated and returns the builder.
            # Writing `.not_("column", "is", None)` CALLS the builder
            # and raises a TypeError.
            .not_.is_("featured_image_url", None)
            # NEWEST FIRST. The rows a reader is looking at are the
            # newest ones.
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::Could not read the unverified rows: {exc}")
        return 2

    rows = response.data or []
    if not rows:
        print("No unverified rows. Nothing to do.")
        print("REFRESH_OK=1")
        return 0

    print(f"Re-resolving the artwork of {len(rows)} row(s).\n")

    confirmed = 0
    unproven = 0
    skipped = 0
    # WHY each row could not be proven, and WHICH outlet it was. A run
    # that says "149 could not be proven" and nothing else is a run
    # that has to be repeated with more printing; a run that says
    # "149 could not be proven: 120 of them are Kantipur, and the page
    # came back empty" is a diagnosis.
    # A Counter, not a plain dict: a reason that has not been seen
    # yet counts as zero instead of raising KeyError, which is what
    # killed the first run of this script at the first row whose page
    # offered no picture. A count that raises is a count that stops
    # the work.
    reasons: Counter[str] = Counter()
    outlets: Counter[str] = Counter()

    async with media_client() as client:
        validator = ImageValidator(
            client, timeout=settings.image_validation_timeout_seconds
        )

        for index, row in enumerate(rows, start=1):
            url = row.get("canonical_url") or row.get("source_article_url")
            if not url:
                skipped += 1
                continue

            outlet = row.get("source_name_snapshot") or "?"

            try:
                page = await extract_from_article(url, client)
                if page.is_empty():
                    unproven += 1
                    reasons["empty page"] += 1
                    outlets[outlet] = outlets.get(outlet, 0) + 1
                    continue

                live = await resolve_live_image(
                    candidate_images(None, page),
                    validator,
                    limit=PROBE_CANDIDATES,
                    declared=page.declared_images,
                    referer=page.source_url,
                )
            except Exception as exc:  # noqa: BLE001 - one row, not the run
                print(f"  {index}/{len(rows)} could not be read: {exc}")
                unproven += 1
                reasons[type(exc).__name__] = reasons.get(type(exc).__name__, 0) + 1
                outlets[outlet] = outlets.get(outlet, 0) + 1
                continue

            if not live:
                unproven += 1
                reasons["no live picture on the page"] += 1
                outlets[outlet] = outlets.get(outlet, 0) + 1
                continue

            if dry_run:
                current = row.get("featured_image_url") or ""
                changed = "same" if current == live else "CHANGED"
                print(
                    f"  {index}/{len(rows)} {changed} "
                    f"{(row.get('headline') or '?')[:60]}"
                )
                confirmed += 1
            else:
                try:
                    await db_call(
                        lambda live=live, row=row: db.table("articles")
                        .update(
                            {
                                "featured_image_url": live,
                                "image_highres_url": live,
                                "image_verified_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                "last_seen_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }
                        )
                        .eq("id", row["id"])
                        .execute()
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {index}/{len(rows)} could not be written: {exc}")
                    unproven += 1
                    continue

                confirmed += 1
                print(
                    f"  {index}/{len(rows)} confirmed "
                    f"{(row.get('headline') or '?')[:70]}"
                )

            if delay:
                await asyncio.sleep(delay)

    print(
        f"\n{confirmed} confirmed, {unproven} could not be proven, "
        f"{skipped} skipped."
    )
    # The diagnosis is only useful if it is PRINTED. "149 could not be
    # proven" says nothing; "149 could not be proven: 120 of them are
    # Kantipur and the page came back empty" says which switch to turn.
    if reasons:
        print("\nWhy each row could not be proven:")
        for reason, count in reasons.most_common():
            print(f"  {count:5d}  {reason}")
    if outlets:
        print("\nWhich outlet each row came from:")
        for outlet, count in outlets.most_common():
            print(f"  {count:5d}  {outlet}")
    if not dry_run:
        print("REFRESH_OK=1")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="How many rows to touch in this run.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_SECONDS,
        help="Seconds to wait between rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Say what would change, and change nothing.",
    )
    args = parser.parse_args()

    return asyncio.run(run(args.limit, args.delay, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
