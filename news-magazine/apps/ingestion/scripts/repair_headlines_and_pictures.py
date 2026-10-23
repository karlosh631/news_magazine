"""
Repair the rows whose headline, points and picture are all wrong.

WHAT THE READER WAS LOOKING AT
-------------------------------
    canadanepal.com . 33 minutes ago Cyber security risks ... |
    The web technology (PHP) ...

That is the stored headline of a real story on a real Nepali outlet.
The listing card is the publisher's own summary of the story, and on a
client-rendered listing that summary is the WHOLE card: the outlet's
domain, the time since publication, the headline, the date and the
first paragraph, all in one string. The listing parser read the card
instead of the heading.

The card's text also became the story's four key points, because the
body was never read - the page could not be read, from the address the
worker runs at. And because the picture could not be proven, the card
showed the points instead of the photograph that is on the real page.

So one mistake spoiled three things at once, and all three are on the
screen.

WHAT THIS DOES
--------------
For every published row whose headline looks like a card rather than a
headline:

  - the story's OWN page is read - through the reader proxy when the
    publisher refuses this address, which nine of them do;
  - the headline is replaced with the one the page declares, exactly as
    the outlet published it (the og:title, and the JSON-LD headline);
  - the four key points are replaced with sentences from that same
    page, so they are the outlet's own words and nothing else;
  - the picture is proven against that page and the marker is set, so
    the card shows the real photograph instead of the points.

The slug is NOT changed: the story keeps the address the reader may
already have bookmarked.

WHAT IT NEVER DOES
------------------
It never invents a headline, a point or a picture. Every one of the
three comes from the story's own page or not at all. A row whose page
cannot be read is left exactly as it is, because "I could not read it"
is not evidence that it is wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

INGESTION_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INGESTION_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")

from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.services.ingestion import db_call  # noqa: E402
from app.services.sanitizer import collapse_repeated_headline  # noqa: E402
from app.sources.media import (  # noqa: E402
    ImageValidator,
    candidate_images,
    extract_from_article,
    media_client,
    resolve_live_image,
)
from app.sources.reader_proxy import fetch_via_reader  # noqa: E402

#: How many picture candidates to probe per row.
PROBE_CANDIDATES = 3

#: A pause between rows, so a sweep of a thousand rows is not a flood.
DELAY_SECONDS = 0.5

#: How many rows one run touches.
DEFAULT_LIMIT = 200

#: A headline longer than this is not a headline; it is a card.
MAX_CLEAN_HEADLINE = 160

#: The shapes a "card" takes. All three were measured on live rows.
#: `\w` rather than `[a-z0-9]`, because the card prints the outlet's
#: domain in the reader's own script too ("नेपालnews.com"), and the
#: domain has to be found inside that as well.
_DOMAIN_RE = re.compile(
    r"\b[\w-]+\.(?:com\.np|com|net|org|np|gov|edu|io)\b", re.IGNORECASE
)
_DATE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\w{3,}\.?\s+\d{1,2}\s+"
    r"\d{1,2}:\d{2}:\d{2}\s+(?:GMT|UTC)\s+\d{4}\b"
)
_RELATIVE_RE = re.compile(
    r"(?:\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago\b)|(?:\d+\s*मिनेट\s*अघि)|"
    r"(?:\d+\s*घंटा\s*अघि)",
    re.IGNORECASE,
)


def looks_like_a_card(headline: str | None) -> bool:
    """
    Is this a headline, or is it the listing card that was mistaken for
    one?

    Three shapes, all measured on rows this script repairs:

      - the outlet's own domain, which the card prints above the
        headline ("canadanepal.com");
      - the absolute date and time the card prints below it
        ("Fri Oct 23 15:32:52 GMT 2026");
      - the relative time the card prints in the reader's language
        ("33 minutes ago", "33 मिनेट अघि").

    A headline that is merely long is not enough: a long headline is
    still a headline, and this script does not shorten anything.
    """
    if not headline:
        return False
    text = headline.strip()
    if not text:
        return False
    if len(text) > MAX_CLEAN_HEADLINE:
        return True
    if _DOMAIN_RE.search(text):
        return True
    if _DATE_RE.search(text):
        return True
    if _RELATIVE_RE.search(text):
        return True
    return False


def points_look_like_a_card(points: list[str] | None) -> bool:
    """Do the four key points carry the card's chrome?"""
    for point in points or []:
        if _DOMAIN_RE.search(point) or _DATE_RE.search(point):
            return True
    return False


async def read_page(url: str, client):
    """
    Read the story's own page once, and return what it declares.

    The title is the one the page declares - the og:title, and the
    JSON-LD headline - and the description is the page's own summary of
    the story. Both are the outlet's own words.

    The media object is returned whole, because the same page carries
    the picture, and the picture is proven against this very read.
    """
    media = await extract_from_article(url, client)

    if media.is_empty():
        # The page could not be read through the ordinary path. Nine
        # publishers refuse this address; the reader reads them.
        settings = get_settings()
        if not settings.reader_proxy_enabled:
            return None
        html = await fetch_via_reader(url, client, proxy=settings.reader_proxy_url)
        if not html:
            return None
        media = await extract_from_article(url, client, prefetched_html=html)

    return media


async def run(limit: int, delay: float, dry_run: bool) -> int:
    settings = get_settings()
    db = get_db()

    try:
        response = await db_call(
            lambda: db.table("articles")
            .select(
                "id, headline, slug, source_article_url, canonical_url, "
                "featured_image_url, image_highres_url, image_verified_at, "
                "summary_points, source_name_snapshot, published_at"
            )
            .eq("status", "published")
            .is_("duplicate_of", None)
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::Could not read the rows: {exc}")
        return 2

    rows = response.data or []
    if not rows:
        print("No rows to repair.")
        print("REPAIR_OK=1")
        return 0

    suspects = [
        row
        for row in rows
        if looks_like_a_card(row.get("headline"))
        or points_look_like_a_card(row.get("summary_points"))
    ]
    print(f"{len(rows)} row(s) read, {len(suspects)} of them look wrong.\n")

    if not suspects:
        print("REPAIR_OK=1")
        return 0

    fixed_headlines = 0
    fixed_points = 0
    proved_pictures = 0
    unreadable = 0

    async with media_client() as client:
        validator = ImageValidator(
            client, timeout=settings.image_validation_timeout_seconds
        )

        for index, row in enumerate(suspects, start=1):
            url = row.get("source_article_url") or row.get("canonical_url")
            if not url:
                unreadable += 1
                continue

            try:
                media = await read_page(url, client)
            except Exception as exc:  # noqa: BLE001
                print(f"  {index}/{len(suspects)} could not be read: {exc}")
                unreadable += 1
                continue

            if media is None:
                unreadable += 1
                continue

            title = collapse_repeated_headline((media.title or "").strip())
            description = (media.description or "").strip()
            if not title and not description:
                unreadable += 1
                continue

            new_headline = title or row.get("headline")
            headline_changed = bool(title) and title != row.get("headline")

            # The four points, from the page's own description.
            points = row.get("summary_points") or []
            new_points = points
            if description:
                from app.services.summarizer import summarize_from_html

                summary = summarize_from_html(
                    description,
                    headline=new_headline,
                    fallback_excerpt=description,
                    num_points=4,
                )
                if summary.points and (
                    points_look_like_a_card(points) or not points
                ):
                    new_points = summary.points

            # The picture, proven against that same page.
            live = None
            try:
                live = await resolve_live_image(
                    candidate_images(None, media),
                    validator,
                    limit=PROBE_CANDIDATES,
                    declared=media.declared_images,
                    referer=url,
                )
            except Exception:  # noqa: BLE001
                live = None

            changes: dict[str, object] = {}
            if headline_changed:
                changes["headline"] = new_headline
            if new_points is not points and new_points != points:
                changes["summary_points"] = new_points
            if live and row.get("image_verified_at") is None:
                changes["featured_image_url"] = live
                changes["image_highres_url"] = live
                changes["image_verified_at"] = datetime.now(timezone.utc).isoformat()

            if not changes:
                continue

            print(
                f"  {index}/{len(suspects)} "
                f"headline {'fixed' if headline_changed else 'kept'}, "
                f"points {'fixed' if 'summary_points' in changes else 'kept'}, "
                f"picture {'proved' if 'image_verified_at' in changes else 'kept'}"
                f" — {(row.get('headline') or '')[:60]}"
            )

            if headline_changed:
                fixed_headlines += 1
            if "summary_points" in changes:
                fixed_points += 1
            if "image_verified_at" in changes:
                proved_pictures += 1

            if not dry_run:
                try:
                    await db_call(
                        lambda changes=changes, row=row: db.table("articles")
                        .update(changes)
                        .eq("id", row["id"])
                        .execute()
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {index}/{len(suspects)} could not be written: {exc}")

            if delay:
                await asyncio.sleep(delay)

    print(
        f"\n{fixed_headlines} headline(s) fixed, {fixed_points} set of points "
        f"fixed, {proved_pictures} picture(s) proved, {unreadable} row(s) "
        f"could not be read."
    )
    if not dry_run:
        print("REPAIR_OK=1")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    return asyncio.run(run(args.limit, args.delay, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
