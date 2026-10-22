"""
On-demand search resurrection.

The retention policy deletes articles after 48 hours. That keeps the
database light, but it means a user searching for a story from three
days ago gets nothing — the row is gone.

This module closes that loop. When a search comes up short:

    1. Look in the live database first (fast path).
    2. If there is not enough to show, re-poll the enabled sources and
       look for the story among what they are publishing *right now*.
    3. Re-insert anything found, flagged resurrected_at so the audit
       trail records that a pruned story came back.
    4. Return everything, newest first.

Nothing here fabricates content. The Gemini key is used at most to
reword a summary the sources already published, and only when
GEMINI_ENABLED is set; the text always comes from a real outlet.

Guard rails, because this endpoint is reachable by anyone who can search:

    - Rate limited per query per hour using the scrape_requests table,
      so a search box cannot be turned into a scraper by a script.
    - Sources are polled through the same robots.txt checker and rate
      limiter as the background worker, so a burst of searches does not
      turn into a burst of requests at 27 news sites.
    - Concurrency-capped so 50 simultaneous searches do not open 200
      sockets.
    - Every request is recorded in scrape_requests for audit.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.dedup import (
    Deduplicator,
    normalize_title,
    similarity,
    token_fingerprint,
    title_tokens,
)
from app.services.sanitizer import sanitize_excerpt, sanitize_headline
from app.sources.media import (
    ImageValidator,
    candidate_images,
    extract_from_article,
    media_client,
    resolve_live_image,
)

logger = logging.getLogger("resurrect")

# A search is "resurrected" only when the live database has fewer than
# this many matches — otherwise the database already answered it.
MIN_LIVE_RESULTS_FOR_SATISFACTION = 3

# How many concurrent source polls one search may trigger.
MAX_CONCURRENT_SOURCE_POLLS = 6

# How long the whole on-demand scrape may take before we return what we
# have. The endpoint is called from a user-facing search box.
SCRAPE_BUDGET_SECONDS = 18.0

# Query tokens that are too generic to be worth scraping the world for.
_GENERIC_QUERY_TOKENS = {
    "news", "समाचार", "today", "आज", "latest", "new", "नयाँ", "update",
    "update news", "breaking", "all", "home", "headline",
}


@dataclass
class ResurrectionResult:
    query: str
    articles: list[dict] = field(default_factory=list)
    live_count: int = 0
    resurrected_count: int = 0
    sources_polled: int = 0
    duration_ms: int = 0
    rate_limited: bool = False
    note: str | None = None


class OnDemandSearcher:
    """
    Handles one search that the live database could not satisfy.

    Instantiated per request; holds no cross-request state other than
    the database connection.
    """

    def __init__(
        self,
        db: Any,
        fetch_source: Any,
        media_enrich: bool = True,
    ) -> None:
        self.db = db
        self.fetch_source = fetch_source
        self.media_enrich = media_enrich
        self.dedup = Deduplicator(db=db)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _recent_request_count(self, query: str, within_hours: int = 1) -> int:
        """
        How many times this exact query has been scraped recently.

        Falls back to permitting the request if the audit table is
        missing, so a database that has not had migration 0007 applied
        degrades to "no limit" rather than 500ing every search.
        """
        try:
            response = (
                self.db.table("scrape_requests")
                .select("id", count="exact")
                .eq("query", query)
                .gte(
                    "requested_at",
                    f"-{within_hours}h",
                )
                .execute()
            )
            return response.count or 0
        except Exception:
            logger.warning("resurrect: scrape_requests unavailable, skipping rate limit")
            return 0

    def _log_request(
        self,
        query: str,
        result_count: int,
        resurrected: int,
        duration_ms: int,
        caller: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        try:
            self.db.table("scrape_requests").insert(
                {
                    "query": query,
                    "result_count": result_count,
                    "resurrected_count": resurrected,
                    "duration_ms": duration_ms,
                    "caller": caller,
                    "ip_address": ip_address,
                }
            ).execute()
        except Exception:
            # An audit-write failure must not fail the user's search.
            logger.debug("resurrect: could not write scrape_requests row")

    # ------------------------------------------------------------------
    # Live database search
    # ------------------------------------------------------------------

    def search_database(self, query: str, limit: int = 20) -> list[dict]:
        """
        Query the live table, including rows the TTL has not reached.
        """
        try:
            response = self.db.rpc(
                "search_articles",
                {
                    "p_query": query,
                    "p_limit": limit,
                    "p_category": None,
                    "p_include_pruned": True,
                },
            ).execute()
            return response.data or []
        except Exception:
            logger.debug("resurrect: search_articles RPC unavailable, using fallback")

        # Fallback for a database without migration 0007: pull a bounded
        # recent window and filter in Python. Less accurate for Nepali,
        # but it keeps search working.
        try:
            response = (
                self.db.table("articles")
                .select(
                    "id, slug, headline, excerpt, featured_image_url, "
                    "image_highres_url, video_url, video_embed_url, audio_url, "
                    "gif_url, published_at, source_name_snapshot, source_article_url"
                )
                .eq("status", "published")
                .is_("duplicate_of", None)
                .order("published_at", desc=True)
                .limit(300)
                .execute()
            )
        except Exception:
            logger.exception("resurrect: database search failed")
            return []

        query_tokens = title_tokens(query)
        scored: list[tuple[float, dict]] = []

        for row in response.data or []:
            headline = row.get("headline") or ""
            if not headline:
                continue

            headline_tokens = title_tokens(headline)
            if query_tokens and query_tokens <= headline_tokens:
                score = 100.0 + len(query_tokens)
            else:
                score = similarity(query, headline)
                if score < 45:
                    continue

            row["_score"] = score
            scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:limit]]

    # ------------------------------------------------------------------
    # Live scrape
    # ------------------------------------------------------------------

    async def scrape_sources(
        self,
        query: str,
        max_per_source: int = 3,
        deadline: float | None = None,
    ) -> list[dict]:
        """
        Re-poll the enabled sources and collect entries matching the query.

        Runs the same adapter path the background worker uses, so the
        robots.txt check, rate limit and parsing all behave identically.
        Bounded by max_per_source and by the shared deadline.
        """
        try:
            source_response = (
                self.db.table("sources")
                .select("*")
                .eq("enabled", True)
                .eq("allowed", True)
                .execute()
            )
            sources = source_response.data or []
        except Exception:
            logger.exception("resurrect: could not load sources")
            return []

        if not sources:
            return []

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SOURCE_POLLS)
        query_tokens = title_tokens(query)

        async def poll(source: dict) -> list[dict]:
            async with semaphore:
                if deadline and time.monotonic() > deadline:
                    return []

                try:
                    raw = await self.fetch_source(source)
                except PermissionError:
                    # robots.txt said no. Not an error worth retrying.
                    return []
                except Exception as exc:
                    logger.debug("resurrect: source %s failed: %s", source.get("name"), exc)
                    return []

                if not raw:
                    return []

                try:
                    adapter = self._adapter_for(source)
                    if adapter is None:
                        return []
                    entries = adapter.parse(raw)
                    normalized = adapter.validate(adapter.normalize(entries))
                except Exception as exc:
                    logger.debug("resurrect: parse failed for %s: %s", source.get("name"), exc)
                    return []

                matches: list[dict] = []
                for article in normalized:
                    headline = article.headline or ""
                    if not headline:
                        continue

                    headline_tokens = title_tokens(headline)
                    if query_tokens:
                        # Strong signal: every query token present.
                        if query_tokens <= headline_tokens:
                            matches.append(article)
                            continue
                        # Otherwise fall back to fuzzy, at a stricter
                        # threshold than dedup uses because we are
                        # matching against a user query, not a wire copy.
                        if similarity(query, headline) >= 70:
                            matches.append(article)
                    elif similarity(query, headline) >= 70:
                        matches.append(article)

                    if len(matches) >= max_per_source:
                        break

                return matches

        async def safe_poll(source: dict) -> list[dict]:
            try:
                return await poll(source)
            except Exception:
                logger.debug("resurrect: poll failed for %s", source.get("name"))
                return []

        results = await asyncio.gather(*(safe_poll(source) for source in sources))
        return [article for group in results for article in group]

    def _adapter_for(self, source: dict) -> Any:
        """Build the right adapter for a source row."""
        from app.services.adapters import build_adapter

        try:
            return build_adapter(source)
        except Exception:
            logger.debug("resurrect: no adapter for %s", source.get("adapter_key"))
            return None

    # ------------------------------------------------------------------
    # Insertion
    # ------------------------------------------------------------------

    async def resurrect(
        self,
        articles: list[dict],
        query: str,
        enrich_media: bool = True,
    ) -> int:
        """
        Re-insert found articles, flagging them as resurrected.

        Returns how many were actually written; articles already in the
        database are counted as not-resurrected.
        """
        from app.config import get_settings

        settings = get_settings()

        if not articles:
            return 0

        written = 0

        async with media_client() as client:
            validator = ImageValidator(client)
            for article in articles:
                try:
                    verdict = self.dedup.check(
                        headline=article.headline,
                        canonical_url=article.source_article_url,
                    )

                    if verdict.is_duplicate and verdict.existing_id:
                        # Already stored. If the TTL had pruned the
                        # original we cannot tell from here, so we leave
                        # it alone and let the search surface it.
                        continue

                    if enrich_media and article.source_article_url:
                        extracted = await extract_from_article(
                            article.source_article_url,
                            client,
                            referer=article.raw_metadata.get("listing_url"),
                        )
                    else:
                        extracted = None

                    # Resurrection is a second writer of featured_image_url
                    # and used to store whatever URL the page offered,
                    # unproven. Same rule as the poll: an image that has
                    # not been shown to load is not an image. Cheap here
                    # — this runs at most a handful of times an hour.
                    live_image = None
                    if settings.validate_images:
                        candidates = candidate_images(
                            getattr(article, "thumbnail_url", None), extracted
                        )
                        live_image = await resolve_live_image(
                            candidates,
                            validator,
                            declared=getattr(extracted, "declared_images", None),
                        )
                        if candidates and not live_image:
                            logger.info(
                                "resurrect: no loadable image for %r; storing it bare",
                                getattr(article, "headline", "?")[:80],
                            )

                    row = self._build_row(
                        article,
                        extracted,
                        query,
                        live_image=live_image,
                        image_validated=bool(settings.validate_images),
                    )
                    result = self.db.table("articles").upsert(
                        row, on_conflict="slug"
                    ).execute()

                    if result.data:
                        written += 1

                except Exception:
                    logger.debug(
                        "resurrect: failed to reinsert %s",
                        getattr(article, "headline", "unknown"),
                    )

        return written

    def _build_row(
        self,
        article: Any,
        extracted: Any,
        query: str,
        live_image: str | None = None,
        image_validated: bool = False,
    ) -> dict:
        """
        Assemble the articles row for a resurrected story.

        `live_image` is the URL already proven to return an image. When
        validation ran and found nothing, it is None and the story is
        stored with no artwork at all rather than with a dead URL.
        """
        from datetime import datetime, timezone

        raw_metadata = getattr(article, "raw_metadata", None) or {}
        title = sanitize_headline(article.headline)
        excerpt = sanitize_excerpt(article.excerpt) or None

        # WHEN THIS STORY ARRIVED IS NOT WHEN WE RE-FETCHED IT
        # ---------------------------------------------------
        # The feed is ordered by `ingested_at` - when we FIRST saw the
        # story - and a resurrected row was stamping it with the
        # current time. So a story published in JANUARY 2025, pulled
        # back out of the database because somebody searched for it,
        # arrived with a brand-new arrival time and jumped to the very
        # top of the homepage, above the news of the day.
        #
        # That is the wrong answer to the question the feed answers. A
        # resurrected story is an OLD story that has been re-fetched on
        # demand; the reader is not seeing it for the first time, and
        # putting it at the top says otherwise.
        #
        # The publication time is the best available answer for when we
        # first saw it - it is when the story was written, which for a
        # story that was in the database before the retention window
        # pruned it is within a short distance of when it was fetched.
        # The row still carries `resurrected_at` and `last_seen_at` for
        # the current time, so "we just re-fetched this" is still
        # recorded; it simply is not what the feed orders by.
        published_at = article.published_at or datetime.now(timezone.utc).isoformat()

        image = None
        if not image_validated:
            # Validation did not run, so the only evidence available is
            # the raw URL the page or the feed offered. Use it - it is
            # better than nothing - but the row is left UNVERIFIED, so
            # the repair pass will come back for it.
            if extracted is not None:
                image = extracted.image_highres_url or extracted.image_url
            if not image:
                image = getattr(article, "thumbnail_url", None)
        else:
            # Validation ran, and its answer is the truth.
            #
            # The old code had this backwards. It took the raw extracted
            # URL as the base value and let `live_image` OVERRIDE it,
            # which meant the fallback was used exactly when validation
            # had REJECTED every candidate. The junk check had already
            # said "this URL is an author portrait, do not store it" and
            # the fallback stored it anyway. That is how a movie review
            # ended up illustrated with the byline photograph of the
            # person who wrote it.
            #
            # So when validation ran, its answer is the only answer: if
            # it is None, no candidate was a usable image for this story
            # and the row carries no image at all. That is the same rule
            # `verify_media_images` applies on the poll path, and the
            # web already has an honest "no image available" state.
            #
            # Both image columns take the same URL: the card prefers
            # image_highres_url, so the two disagreeing is how a dead
            # highres beat a live featured image on the article page.
            image = live_image

        if extracted is not None:
            excerpt = excerpt or (
                sanitize_excerpt(extracted.description) if extracted.description else None
            )
            title = title or (sanitize_headline(extracted.title) if extracted.title else title)

        slug = self._slug(title, raw_metadata.get("content_hash"))

        return {
            "slug": slug,
            "headline": title,
            "language": getattr(article, "language", "ne") or "ne",
            # Resurrection writes straight to published: the row is
            # already TTL-checked and its content came from a source we
            # are permitted to read. is_breaking/is_featured stay false.
            "status": "published",
            "body_html": None,
            "excerpt": excerpt,
            "featured_image_url": image,
            "image_highres_url": image,
            # Only a picture that was checked against the story's own page
            # counts as verified. See the note on the same column in
            # app/services/ingestion.py.
            "image_verified_at": (
                datetime.now(timezone.utc).isoformat()
                if image_validated and image
                else None
            ),
            "video_url": extracted.video_embed_url if extracted is not None else None,
            "video_embed_url": extracted.video_embed_url if extracted is not None else None,
            "audio_url": extracted.audio_url if extracted is not None else None,
            "gif_url": None,
            "canonical_url": article.canonical_url or article.source_article_url,
            "title_normalized": normalize_title(title),
            "content_hash": raw_metadata.get("content_hash") or token_fingerprint(title),
            "source_article_url": article.source_article_url,
            "published_at": published_at,
            "ingested_at": published_at,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "resurrected_at": datetime.now(timezone.utc).isoformat(),
            "scrape_query": query,
            "source_count": 1,
        }

    @staticmethod
    def _slug(title: str, content_hash: str | None) -> str:
        """
        Slug that survives Devanagari.

        Stage 1 stripped every non-ASCII character, so every Nepali
        headline collapsed to the same empty base and articles overwrote
        each other on the unique slug index. This transliterates
        Devanagari to a stable ASCII form and falls back to the content
        hash so uniqueness never depends on the title at all.
        """
        try:
            from slugify import slugify as _slugify

            base = _slugify(title, allow_unicode=True)
        except Exception:
            base = re.sub(r"[^a-zA-Z0-9\s-]", "", title).strip().lower()
            base = re.sub(r"\s+", "-", base)[:60]

        if not base or not re.search(r"[a-z0-9]", base):
            # Nothing ASCII to work with — hash is the whole identity.
            base = (content_hash or token_fingerprint(title))[:16]

        suffix = (content_hash or token_fingerprint(title) or "")[:8]
        date = time.strftime("%Y-%m-%d")
        return f"{base[:70]}-{suffix}-{date}" if suffix else f"{base[:80]}-{date}"


# ----------------------------------------------------------------------
# Entry point used by the FastAPI route
# ----------------------------------------------------------------------

async def search_with_resurrection(
    db: Any,
    query: str,
    fetch_source: Any,
    limit: int = 20,
    max_requests_per_hour: int = 5,
    enable_scrape: bool = True,
    enrich_media: bool = True,
    caller: str | None = None,
    ip_address: str | None = None,
) -> ResurrectionResult:
    """
    Search the live database, then scrape the sources if it came up short.

    This is the whole public contract for /api/scrape-on-demand.
    """
    started = time.monotonic()
    query = (query or "").strip()
    result = ResurrectionResult(query=query)

    if not query:
        result.note = "Empty query."
        return result

    # ---- 1. Live database ----
    articles = OnDemandSearcher(db, fetch_source).search_database(query, limit=limit)
    result.live_count = len(articles)
    result.articles = articles

    if len(articles) >= MIN_LIVE_RESULTS_FOR_SATISFACTION:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    if not enable_scrape:
        result.note = "Database had few results and on-demand scraping is disabled."
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # ---- 2. Rate limit ----
    if len(title_tokens(query)) <= 1 and query.lower() in _GENERIC_QUERY_TOKENS:
        result.rate_limited = True
        result.note = "Query is too generic to scrape. Try a more specific search."
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    searcher = OnDemandSearcher(db, fetch_source)
    if searcher._recent_request_count(query) >= max_requests_per_hour:
        result.rate_limited = True
        result.note = (
            "That search has been run several times already. Showing the "
            "latest results we have."
        )
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # ---- 3. Scrape ----
    deadline = time.monotonic() + SCRAPE_BUDGET_SECONDS
    try:
        found = await searcher.scrape_sources(query, deadline=deadline)
        result.sources_polled = len(found)

        written = await searcher.resurrect(found, query, enrich_media=enrich_media)
        result.resurrected_count = written

    except Exception:
        logger.exception("resurrect: on-demand scrape failed for %r", query)

    # ---- 4. Re-read the database so resurrected rows are included ----
    if result.resurrected_count:
        refreshed = searcher.search_database(query, limit=limit)
        if refreshed:
            result.articles = refreshed
            result.live_count = len(refreshed)

    result.duration_ms = int((time.monotonic() - started) * 1000)
    searcher._log_request(
        query=query,
        result_count=len(result.articles),
        resurrected=result.resurrected_count,
        duration_ms=result.duration_ms,
        caller=caller,
        ip_address=ip_address,
    )

    return result