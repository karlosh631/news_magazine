"""
Find the rows whose picture is not the story's, and put the story's own
picture back.

THE TWO FAULTS THIS REPAIRS
----------------------------
There were two ways a card could carry a photograph of something else,
and both of them were made possible by one thing: the row was written
by an ingestion build older than the rules that now decide which picture
belongs to a story.

  1. THE STORED PICTURE IS A RESIZER. Kathmandu Post serves its body
     pictures through `assets-api.../thumb.php?src=<the real file>`.
     The current code drops a resizer outright - it is a proxy for a
     file that lives somewhere else - so a stored resizer is proof
     that the row was written before that rule existed.

     Measured on production on 2026-10-23: the story "Hacking attempts
     surge as firms race to patch PHP flaws" carried the black-and-
     white photograph of a baby elephant lifted by its mother, which is
     the "Most Read from World" thumbnail that Kathmandu Post puts on
     every one of its article pages. The URL was a thumb.php of a
     third-party file. The story's own og:image is a different
     photograph entirely, and the current code finds it: running the
     repo's own ranking against the live page returns the og:image and
     nothing else.

  2. THE STORED PICTURE IS ON MORE THAN ONE STORY. A photograph that
     belongs to one story does not belong to two. When the same URL is
     stored against several different stories, all but one of them are
     wrong, and there is no need to fetch a single page to know that.

WHAT IT DOES
------------
For each suspect row it fetches the story's OWN page, asks the repo's
own extractor which picture that page offers as the story's, and:

  * if the page proves a different picture, the row gets that picture
    and the marker is set - the site will show it, because it is now
    confirmed against the story's own page;
  * if the page cannot be read, the row is LEFT ALONE. A page that
    cannot be fetched is not evidence that the picture is wrong, and a
    transient failure must not cost a story its artwork;
  * if the page is read and offers no picture of its own, the stored
    picture is left in place but the MARKER is cleared, so the site
    stops showing it. An unconfirmed picture is not shown, and a story
    without a picture is honest - that is the operator's rule.

WHAT IT NEVER DOES
------------------
It never invents a picture. It never takes one from a feed. It never
removes a picture it cannot replace with a better one.

This is a REPAIR script, not part of the poll: it is meant to be run
once, and again whenever a new backlog appears, from GitHub Actions.
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
    _is_resizer,
    candidate_images,
    extract_from_article,
    media_client,
    resolve_live_image,
)

#: How many picture candidates are probed per row. The same number the
#: poll's own store path uses, so a repaired row is held to exactly the
#: standard a freshly stored one is.
PROBE_CANDIDATES = 3

#: A pause between rows. One page fetch per row, one request a second
#: at the slow end: the rows are spread over a few dozen publishers,
#: and a publisher asked for eight hundred pages in ten minutes is a
#: publisher that starts refusing.
DELAY_SECONDS = 1.0

#: How many rows one run will touch.
DEFAULT_LIMIT = 250

#: How many stories may share one picture before the picture is treated
#: as suspect. Two is already a mistake - a photograph belongs to one
#: story - but one is left alone because the same picture can be
#: published twice legitimately: once by the outlet that took it and
#: once by an outlet that syndicated the story.
SHARED_PICTURE_LIMIT = 2


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
            .not_.is_("featured_image_url", None)
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::Could not read the published rows: {exc}")
        return 2

    rows = response.data or []
    if not rows:
        print("No published rows with a picture. Nothing to do.")
        print("FIX_OK=1")
        return 0

    # ---- Who is suspect, and why -------------------------------------
    #
    # A picture shared by several stories is suspect for every story
    # but one. The count is over the rows this run can see, which is
    # the newest slice of the table: a picture on two rows that are
    # both here is a picture on two stories.
    usage: Counter[str] = Counter()
    for row in rows:
        url = row.get("featured_image_url")
        if url:
            usage[url] += 1

    resizers: list[dict] = []
    shared: list[dict] = []
    seen_resizer: set[str] = set()
    seen_shared: set[str] = set()

    for row in rows:
        url = row.get("featured_image_url") or ""
        if not url:
            continue
        row_id = row.get("id")

        # A resizer is never stored by the current code.
        if _is_resizer(url) and row_id not in seen_resizer:
            seen_resizer.add(row_id)
            resizers.append(row)

        # A picture on more than one story.
        if usage[url] >= SHARED_PICTURE_LIMIT and row_id not in seen_shared:
            seen_shared.add(row_id)
            shared.append(row)

    suspects: dict[str, dict] = {}
    for row in resizers:
        suspects[row["id"]] = {**row, "why": ["stored picture is a resizer"]}
    for row in shared:
        entry = suspects.setdefault(
            row["id"], {**row, "why": []}
        )
        entry["why"] = list(entry.get("why", [])) + [
            f"picture is shared by {usage[row['featured_image_url']]} stories"
        ]

    ordered = sorted(suspects.values(), key=lambda r: r.get("published_at") or "")
    ordered.reverse()

    print(f"{len(rows)} published row(s) with a picture.")
    print(f"{len(resizers)} carry a resizer URL.")
    print(f"{len(shared)} share their picture with another story.")
    print(f"{len(ordered)} suspect row(s) to check.\n")

    if not ordered:
        print("No suspect rows. The pictures belong to their stories.")
        print("FIX_OK=1")
        return 0

    corrected = 0
    unproven = 0
    unreadable = 0
    unchanged = 0
    reasons: dict[str, int] = {}

    async with media_client() as client:
        validator = ImageValidator(
            client, timeout=settings.image_validation_timeout_seconds
        )

        for index, row in enumerate(ordered, start=1):
            url = row.get("canonical_url") or row.get("source_article_url")
            if not url:
                unproven += 1
                continue

            try:
                page = await extract_from_article(url, client)
                if page.is_empty():
                    # The page could not be read. That is not evidence
                    # that the picture is wrong, so the row is left
                    # exactly as it is.
                    unreadable += 1
                    reasons["page could not be read"] = (
                        reasons.get("page could not be read", 0) + 1
                    )
                    continue

                live = await resolve_live_image(
                    candidate_images(None, page),
                    validator,
                    limit=PROBE_CANDIDATES,
                    declared=page.declared_images,
                    referer=page.source_url,
                )
            except Exception as exc:  # noqa: BLE001 - one row, not the run
                print(f"  {index}/{len(ordered)} could not be read: {exc}")
                unreadable += 1
                reasons[type(exc).__name__] = reasons.get(type(exc).__name__, 0) + 1
                continue

            current = row.get("featured_image_url") or ""

            if not live:
                # The page was read and offers no picture of its own.
                # The stored picture may or may not be the story's, so
                # it stays in the row - but the MARKER goes, and the
                # site then shows the story without a picture, which is
                # honest. An unconfirmed picture is not shown.
                if row.get("image_verified_at"):
                    if not dry_run:
                        try:
                            await db_call(
                                lambda row=row: db.table("articles")
                                .update({"image_verified_at": None})
                                .eq("id", row["id"])
                                .execute()
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"  {index}/{len(ordered)} could not be written: {exc}")
                            unproven += 1
                            continue
                    print(
                        f"  {index}/{len(ordered)} marker cleared - the page offers "
                        f"no picture of its own: "
                        f"{(row.get('headline') or '?')[:70]}"
                    )
                    unproven += 1
                    reasons["no picture on the page"] = (
                        reasons.get("no picture on the page", 0) + 1
                    )
                else:
                    unchanged += 1
                continue

            if live == current:
                unchanged += 1
                continue

            if dry_run:
                print(
                    f"  {index}/{len(ordered)} would change "
                    f"{(row.get('headline') or '?')[:60]}"
                )
                corrected += 1
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
                                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        .eq("id", row["id"])
                        .execute()
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {index}/{len(ordered)} could not be written: {exc}")
                    unproven += 1
                    continue

                corrected += 1
                print(
                    f"  {index}/{len(ordered)} corrected "
                    f"{(row.get('headline') or '?')[:70]}"
                )
                print(f"      was {current[-90:]}")
                print(f"      now {live[-90:]}")

            if delay:
                await asyncio.sleep(delay)

    print(
        f"\n{corrected} corrected, {unchanged} already right, "
        f"{unproven} could not be proven, {unreadable} could not be read."
    )
    if reasons:
        print("Reasons:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>4}  {reason}")
    if not dry_run:
        print("FIX_OK=1")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="How many published rows to look at.",
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
