"""
Orchestrates one full sync run for one source:

    fetch -> parse -> normalize -> validate
        -> enrich (og:image / video / audio from the article page)
        -> dedupe (exact -> fingerprint -> fuzzy, 12h window)
        -> categorize -> sanitize -> store (UPSERT) -> log

A failure on one source never affects another.

What changed from Stage 1, and why
----------------------------------

1. Gemini "IEEE paper synthesis" is off the storage path entirely.

   Stage 1 called Gemini on every article and stored the returned IEEE
   title as the news headline, which rewrote real news into academic
   paper titles. It also meant a Gemini quota error rejected 100% of
   articles while blocking the event loop for 20s per item via
   synchronous httpx.post. `synthesize_summary` is now optional,
   async, failure-tolerant, and only ever produces an excerpt — never
   a headline.

2. Deduplication actually deduplicates.

   Stage 1 never wrote `title_normalized` (the column its own detector
   queried) and derived `content_hash` from the outlet's URL, so
   cross-outlet syndication could not be detected: the same wire story
   was inserted once per outlet. See app/services/dedup.py.

3. Media comes from the article page, not just the feed.

   Stage 1 read only feed-provided thumbnails, which is why images
   rendered blank. See app/sources/media.py.

4. Pruning is a real TTL.

   Stage 1 had no age-based pruning at all — just a row cap that, when
   exceeded, deleted 20% of the entire table in one call. Retention is
   now 48h via the prune_expired_articles() SQL function.

5. Supabase calls run in a thread executor.

   The supabase-py client is synchronous. Calling it directly from an
   async scheduler blocks every other source's job on the event loop.

Security principles preserved:
- Only enabled + allowed sources are processed.
- The adapter refuses to fetch when robots.txt disallows it.
- Headlines and excerpts are sanitized before storage.
- Full article HTML is never stored (republish_permission stays false
  for every seeded outlet).
- Database writes UPSERT on a unique constraint, so a re-poll refreshes
  media URLs instead of erroring.
- A broken article never stops the rest of the feed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.database import get_db
from app.services.adapters import build_adapter
from app.sources.html_listing import HtmlListingAdapter
from app.services.categorizer import Categorizer
from app.services.dedup import (
    Deduplicator,
    normalize_title,
    token_fingerprint,
)
from app.services.sanitizer import (
    build_safe_paragraph,
    sanitize_excerpt,
    sanitize_headline,
)
from app.services.summarizer import (
    polish_with_gemini,
    summarize_from_html,
)
from app.sources.media import (
    ExtractedMedia,
    best_image,
    extract_from_article,
    media_client,
    upgrade_to_highres,
)

logger = logging.getLogger("ingestion")

# Off-thread execution for the synchronous supabase-py client.
# Sized to the scheduler's max_instances, not the CPU count: these are
# I/O-bound waits on a network socket, not CPU work.
DB_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="supabase")


async def db_call(fn, *args, **kwargs):
    """Run a blocking supabase-py call off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(DB_EXECUTOR, lambda: fn(*args, **kwargs))


# =============================================================
# RESULT
# =============================================================

@dataclass
class IngestionResult:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    rejected: int = 0
    enriched: int = 0
    errors: int = 0
    pruned: int = 0
    dedup_detail: str = ""
    error_details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "new": self.new,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "enriched": self.enriched,
            "errors": self.errors,
            "pruned": self.pruned,
            "dedup_detail": self.dedup_detail,
        }


# =============================================================
# OPTIONAL GEMINI ENRICHMENT
#
# Off by default and never on the critical path. Used only to write a
# better excerpt from text the source already published. It never
# touches the headline and never gates the insert.
# =============================================================

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent"
)


def _gemini_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("Gemini_API_Key")


async def synthesize_summary(
    headline: str,
    excerpt: str | None,
    sector: str,
) -> str | None:
    """
    Optional: a one-paragraph summary written from the outlet's own text.

    Returns None on any failure — the caller stores the original excerpt.
    Deliberately has a short timeout and no retry: this is a nice-to-have
    and must never delay or block ingestion.
    """
    if os.getenv("ENABLE_GEMINI_SUMMARY", "false").lower() != "true":
        return None

    api_key = _gemini_key()
    if not api_key:
        return None

    source_text = (excerpt or "").strip()
    if len(source_text) < 80:
        # Too little to summarise without inventing detail.
        return None

    prompt = (
        "Summarise the following news excerpt into exactly one sentence of "
        f"{sector} news copy. Use only facts present in the excerpt. "
        "Do not add names, numbers, dates or context that are not written there. "
        "Return plain text only.\n\n"
        f"HEADLINE: {headline}\n\nEXCERPT:\n{source_text[:1500]}"
    )

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                GEMINI_ENDPOINT,
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": 300,
                    },
                },
            )
            response.raise_for_status()
            text = (
                response.json()["candidates"][0]["content"]["parts"][0]["text"]
                .strip()
            )
        return sanitize_excerpt(text) or None
    except Exception:
        # Enrichment is best-effort by definition.
        logger.debug("gemini summary skipped for %r", headline[:60])
        return None


# =============================================================
# SLUG
# =============================================================

def make_slug(title: str, sector: str | None, content_hash: str | None) -> str:
    """
    Slug that survives Devanagari and is deterministic.

    Stage 1 stripped every non-ASCII character, so every Nepali headline
    collapsed to an empty base: two Nepali articles published the same
    day produced the SAME slug and the second silently overwrote the
    first on the unique index. Now:

    - Devanagari is transliterated when python-slugify is available.
    - Without it, the content hash becomes the whole identity, so
      uniqueness never depends on being able to transliterate.
    - The content hash is always part of the slug, so two outlets filing
      the same story under the same words cannot collide.
    """
    try:
        from slugify import slugify as _slugify

        base = _slugify(title, allow_unicode=False, max_length=70)
    except Exception:
        base = re.sub(r"[^a-zA-Z0-9\s-]", "", title).strip().lower()
        base = re.sub(r"\s+", "-", base)[:70]

    base = (base or "").strip("-")

    # Nothing transliteratable (a fully-Devanagari headline without
    # python-slugify, or a headline of only punctuation): fall back to
    # the hash so the slug is still unique and stable.
    if not base or not re.search(r"[a-z0-9]", base):
        base = (content_hash or token_fingerprint(title))[:16]

    sector_part = re.sub(r"[^a-z0-9]+", "-", (sector or "news").lower()).strip("-")
    suffix = (content_hash or token_fingerprint(title) or "")[:8]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    parts = [part for part in (sector_part, base, suffix, date_str) if part]
    return "-".join(parts)[:180]


# =============================================================
# MEDIA EXTRACTION
# =============================================================

def media_from_feed(article: Any) -> dict[str, Any]:
    """
    Media advertised by the feed itself.

    Kept as the fallback for every field the article page cannot
    improve on, because a feed enclosure is often the outlet's own
    chosen asset.
    """
    raw_metadata = getattr(article, "raw_metadata", None) or {}

    def meta(*keys: str) -> str | None:
        for key in keys:
            value = raw_metadata.get(key)
            if value:
                return str(value)
        return None

    feed_image = (
        getattr(article, "thumbnail_url", None)
        or meta("image_url", "thumbnail_url", "og_image", "media_image")
    )

    return {
        "image_url": feed_image,
        "video_url": meta("video_url", "enclosure_video_url", "media_video"),
        "audio_url": meta("audio_url", "enclosure_audio_url", "media_audio", "podcast_url"),
        "gif_url": meta("gif_url", "animated_image_url", "media_gif"),
    }


def merge_media(
    feed_media: dict[str, Any],
    page: ExtractedMedia | None,
) -> dict[str, Any]:
    """
    Combine feed-provided and page-scraped media.

    Page-scraped values win where they exist: og:image is the rendition
    the outlet picked for sharing, which is exactly what a feed
    thumbnail is not. The feed fills every gap.
    """
    image = None
    if page is not None and page.image_url:
        image = page.image_highres_url or page.image_url
    if not image:
        image = feed_media.get("image_url")

    return {
        "image_url": image,
        "image_highres_url": (
            (page.image_highres_url or page.image_url)
            if page is not None and page.image_url
            else upgrade_to_highres(feed_media.get("image_url"))
        ),
        "video_url": (
            (page.video_embed_url or page.video_id and page.video_thumbnail_url)
            if page is not None
            else feed_media.get("video_url")
        ) or feed_media.get("video_url"),
        "video_embed_url": page.video_embed_url if page is not None else None,
        "audio_url": (
            (page.audio_url if page is not None else None) or feed_media.get("audio_url")
        ),
        "gif_url": feed_media.get("gif_url"),
        "image_alt": page.image_alt if page is not None else None,
        "word_count": page.word_count if page is not None else None,
    }


# =============================================================
# CATEGORY LOADING
# =============================================================

async def load_categories(db) -> tuple[Categorizer, dict[str, str]]:
    """
    Load the category table into a slug -> uuid map, off-thread.

    Returns the national category id too: it is the fallback for any
    article no keyword rule matches, so an article is never left with a
    NULL primary_category_id and therefore invisible on every category
    page.
    """
    response = await db_call(
        lambda: db.table("categories").select("id, slug").execute()
    )
    rows = response.data or []
    slug_to_id = {
        row["slug"]: row["id"] for row in rows if row.get("slug") and row.get("id")
    }
    categorizer = Categorizer(slug_to_id)
    categorizer.default_category_id = slug_to_id.get("national")
    return categorizer, slug_to_id


# =============================================================
# STORE
# =============================================================

async def _upsert_article_source(
    db, article_id, source_id, source_article_url, is_primary=False
) -> bool:
    """
    Record that this outlet also carried the story.

    Stage 1 upserted on `source_id,source_article_url`, a unique
    constraint that does not exist in the schema — so every call raised
    and the table stayed permanently empty, which is why the
    multi-outlet merge record never appeared. Migration 0009 adds the
    real constraint; until it is applied we fall back to a read-then-
    write so the relationship is still recorded.
    """
    if not article_id or not source_id or not source_article_url:
        return False

    payload = {
        "article_id": article_id,
        "source_id": source_id,
        "source_article_url": source_article_url,
        "is_primary": is_primary,
    }

    try:
        await db_call(
            lambda: db.table("article_sources")
            .upsert(payload, on_conflict="article_id,source_id")
            .execute()
        )
        return True
    except Exception:
        logger.debug(
            "article_sources upsert on (article_id,source_id) unavailable — "
            "apply migration 0009 to enable it"
        )

    # Fallback: read then insert. Not atomic, but the unique index added
    # in 0009 makes a duplicate insert a no-op rather than a crash.
    try:
        existing = await db_call(
            lambda: db.table("article_sources")
            .select("id")
            .eq("article_id", article_id)
            .eq("source_id", source_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            return True
        await db_call(
            lambda: db.table("article_sources").insert(payload).execute()
        )
        return True
    except Exception:
        logger.exception("Failed to record article_sources relationship")
        return False


async def _bump_source_count(db, article_id: str) -> None:
    """
    Another outlet carried this story; increment the counter.

    Uses an atomic SQL increment rather than read-then-write: two
    sources polled concurrently would otherwise both read 1, both write
    2, and one increment would be lost.
    """
    try:
        await db_call(
            lambda: db.rpc(
                "increment_article_source_count", {"p_article_id": article_id}
            ).execute()
        )
    except Exception:
        logger.debug("source_count bump failed for %s (migration 0009 not applied?)", article_id)


async def store_article(
    db,
    article: Any,
    media: dict[str, Any],
    category_id: str | None,
    source_row: dict,
    content_hash: str,
    title_normalized: str,
    excerpt: str | None,
    summary: Any | None = None,
) -> str | None:
    """
    Write one article. Returns its id.

    status is 'published' because the outlet's own headline and image are
    being republished under attribution, which is what
    `attribution_required` exists to make explicit. The editorial
    pipeline in ARCHITECTURE.md applies to platform-authored articles,
    not to syndicated wire headlines.

    summary_points is a JSON array of exactly four sentences lifted
    verbatim from the outlet's article — see services/summarizer.py. It
    is never generated text.
    """
    now = datetime.now(timezone.utc).isoformat()
    slug = make_slug(article.headline, source_row.get("sector"), content_hash)

    row = {
        "slug": slug,
        "headline": article.headline,
        "language": article.language or source_row.get("default_language", "ne"),
        "status": "published",
        "body_html": build_safe_paragraph(excerpt) if excerpt else None,
        "excerpt": excerpt,
        "summary_points": (
            summary.points[:4] if summary and summary.points else None
        ),
        "summary_method": (
            summary.method if summary and summary.points else None
        ),
        "featured_image_url": media.get("image_url"),
        "featured_image_alt": media.get("image_alt"),
        "image_highres_url": media.get("image_highres_url"),
        "video_url": media.get("video_url"),
        "video_embed_url": media.get("video_embed_url"),
        "audio_url": media.get("audio_url"),
        "gif_url": media.get("gif_url"),
        "source_id": source_row["id"],
        # The link out to the original article. The card's "Read the full
        # story" action uses this, so it must survive every upsert.
        "source_article_url": article.source_article_url,
        "source_name_snapshot": source_row.get("name", "Unknown Source"),
        "primary_category_id": category_id,
        "canonical_url": article.canonical_url or article.source_article_url,
        # The column Stage 1's own detector queried but never wrote.
        "title_normalized": title_normalized,
        "content_hash": content_hash,
        "word_count": media.get("word_count"),
        "published_at": article.published_at or now,
        "ingested_at": now,
        "last_seen_at": now,
        "source_count": 1,
    }

    response = await db_call(
        lambda: db.table("articles").upsert(row, on_conflict="slug").execute()
    )
    return (response.data or [{}])[0].get("id")


# =============================================================
# PRUNING
# =============================================================

async def prune_old_articles(db, retention_hours: int, max_rows: int) -> int:
    """
    Delete articles past their TTL, then enforce the row cap.

    Delegates to the prune_expired_articles() SQL function. Stage 1 did
    this in Python and, once over the cap, deleted 20% of the whole
    table in a single call — thousands of valid articles removed to
    clear an overshoot of one row.
    """
    try:
        response = await db_call(
            lambda: db.rpc(
                "prune_expired_articles",
                {"p_retention_hours": retention_hours, "p_max_rows": max_rows},
            ).execute()
        )
        deleted = response.data or 0
        if deleted:
            logger.info("Pruned %s articles past %sh TTL.", deleted, retention_hours)
        return int(deleted)
    except Exception:
        logger.exception("TTL prune failed; articles will age out next cycle")
        return 0


# =============================================================
# LOGGING HELPERS
# =============================================================

async def _log_error(
    db, source_id, sync_log_id, error_type, message, context=None
) -> None:
    logger.error("[%s] %s: %s", source_id, error_type, message)
    try:
        await db_call(
            lambda: db.table("source_errors")
            .insert(
                {
                    "source_id": source_id,
                    "sync_log_id": sync_log_id,
                    "error_type": error_type,
                    "message": str(message)[:5000],
                    "context": context,
                }
            )
            .execute()
        )
    except Exception:
        logger.exception("Failed to write source error to database")


async def _finish_sync_log(db, sync_log_id, status, result: IngestionResult) -> None:
    try:
        await db_call(
            lambda: db.table("source_sync_logs")
            .update(
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "status": status,
                    "fetched_count": result.fetched,
                    "new_count": result.new,
                    "duplicate_count": result.duplicates,
                    "rejected_count": result.rejected,
                    "error_count": result.errors,
                    "details": result.as_dict(),
                }
            )
            .eq("id", sync_log_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to update sync log %s", sync_log_id)


# =============================================================
# MAIN SOURCE SYNC
# =============================================================

async def run_source_sync(
    source_row: dict,
    enrich_media: bool = True,
    prune: bool = True,
) -> IngestionResult:
    """
    Run the full pipeline for one source. Never raises.
    """
    from app.config import get_settings

    settings = get_settings()
    db = get_db()
    result = IngestionResult()
    source_id = source_row.get("id")

    if not source_id:
        logger.error("Source row has no id; skipping.")
        return result

    # ---- 1. Sync log ----
    try:
        response = await db_call(
            lambda: db.table("source_sync_logs")
            .insert({"source_id": source_id, "status": "running"})
            .execute()
        )
    except Exception as exc:
        result.errors += 1
        result.error_details.append({"type": "sync_log_create_error", "message": str(exc)})
        logger.exception("Unable to create sync log for source %s", source_id)
        return result

    if not response.data:
        result.errors += 1
        logger.error("Sync log insert returned no row for source %s", source_id)
        return result

    sync_log_id = response.data[0]["id"]

    async def fail(error_type: str, message: str) -> IngestionResult:
        result.errors += 1
        result.error_details.append({"type": error_type, "message": str(message)})
        await _log_error(db, source_id, sync_log_id, error_type, message)
        await _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    # ---- 2. Permission gate ----
    if not source_row.get("enabled") or not source_row.get("allowed"):
        return await fail(
            "source_disabled",
            "Source is not enabled+allowed; refusing to fetch.",
        )

    # ---- 3. Adapter ----
    try:
        adapter = build_adapter(source_row)
    except KeyError as exc:
        return await fail("unknown_adapter", str(exc))
    except Exception as exc:
        return await fail("adapter_initialization_error", str(exc))

    # ---- 4. Categories ----
    try:
        categorizer, slug_to_category = await load_categories(db)
        national_category_id = slug_to_category.get("national")
    except Exception as exc:
        return await fail("category_load_error", str(exc))

    dedup = Deduplicator(db=db, window_hours=settings.dedup_window_hours)

    # ---- 5. Fetch / parse ----
    try:
        raw = await adapter.fetch()
        entries = adapter.parse(raw)
        result.fetched = len(entries)
        articles = adapter.validate(adapter.normalize(entries))

        # The HTML adapter's link heuristic is permissive by design, so
        # it verifies each discovered URL actually resolves to an article
        # before we store it. Without this, a client-rendered listing put
        # 77 dead links in the articles table.
        if isinstance(adapter, HtmlListingAdapter) and adapter.verify_urls:
            before = len(articles)
            articles = await adapter.verify_normalized(articles)
            dropped = before - len(articles)
            if dropped:
                result.rejected += dropped
                logger.info(
                    "%s: %d listing link(s) were not articles and were dropped",
                    source_row.get("name"), dropped,
                )
    except PermissionError as exc:
        return await fail("not_permitted", str(exc))
    except Exception as exc:
        logger.exception("Fetch/parse failed for source %s", source_id)
        return await fail("fetch_or_parse_error", str(exc))

    if prune:
        result.pruned = await prune_old_articles(
            db, settings.retention_hours, settings.max_article_rows
        )

    # ---- 6. Per-article pipeline ----
    async with media_client() as media_http:

        for article in articles:
            try:
                if not article.headline or not article.source_article_url:
                    result.rejected += 1
                    continue

                headline = sanitize_headline(article.headline)
                if not headline:
                    result.rejected += 1
                    await _log_error(
                        db, source_id, sync_log_id, "empty_headline",
                        "Headline was empty after sanitization.",
                        {"source_url": article.source_article_url},
                    )
                    continue

                excerpt = sanitize_excerpt(article.excerpt) or None

                # ---- Deduplicate ----
                verdict = dedup.check(
                    headline=headline,
                    canonical_url=article.canonical_url or article.source_article_url,
                )

                if verdict.is_duplicate and verdict.existing_id:
                    result.duplicates += 1
                    await _upsert_article_source(
                        db=db,
                        article_id=verdict.existing_id,
                        source_id=source_id,
                        source_article_url=article.source_article_url,
                        is_primary=False,
                    )
                    await _bump_source_count(db, verdict.existing_id)
                    logger.info(
                        "Duplicate (%s, %.0f%%): %r <- %r",
                        verdict.reason, verdict.confidence,
                        headline, verdict.matched_title,
                    )
                    continue

                # ---- Enrich from the article page ----
                feed_media = media_from_feed(article)
                extracted: ExtractedMedia | None = None

                needs_fetch = bool(
                    (article.raw_metadata or {}).get("needs_article_fetch")
                ) or not feed_media.get("image_url")

                if enrich_media and article.source_article_url and (needs_fetch or not feed_media.get("image_url")):
                    extracted = await extract_from_article(
                        article.source_article_url,
                        media_http,
                        referer=(article.raw_metadata or {}).get("listing_url"),
                    )
                    if not extracted.is_empty():
                        result.enriched += 1

                media = merge_media(feed_media, extracted)

                # Use the richer description when the feed gave us none.
                if not excerpt and extracted is not None:
                    excerpt = sanitize_excerpt(extracted.description) or None

                # ---- Four-point extractive summary ----
                # Every point is a verbatim sentence from the article the
                # outlet published. Nothing is generated.
                summary = summarize_from_html(
                    extracted.article_text if extracted is not None else None,
                    headline=headline,
                    fallback_excerpt=excerpt,
                    num_points=4,
                )

                if summary.points and settings.enable_gemini_ranking:
                    summary.points = await polish_with_gemini(
                        summary.points, headline, enabled=True
                    )
                    summary.method = f"{summary.method}+gemini_ranked"

                # ---- Category ----
                # The categoriser returns None when no keyword rule
                # matches. Falling through to the source's default (which
                # is usually NULL) left the article with no category at
                # all, so it appeared on no category page — the
                # /category/national page rendered empty while hundreds
                # of articles existed. Default to national instead.
                category_id = (
                    categorizer.classify(headline, excerpt)
                    or source_row.get("default_category_id")
                    or national_category_id
                )

                # ---- Identity ----
                title_normalized = normalize_title(headline)
                fingerprint = token_fingerprint(headline)
                content_hash = (
                    (article.raw_metadata or {}).get("content_hash")
                    or hashlib.sha256(
                        f"{article.canonical_url or article.source_article_url}|{fingerprint}".encode("utf-8")
                    ).hexdigest()
                )

                # ---- Store ----
                article_id = await store_article(
                    db=db,
                    article=article,
                    media=media,
                    category_id=category_id,
                    source_row=source_row,
                    content_hash=content_hash,
                    title_normalized=title_normalized,
                    excerpt=excerpt,
                    summary=summary,
                )

                if article_id:
                    await _upsert_article_source(
                        db=db,
                        article_id=article_id,
                        source_id=source_id,
                        source_article_url=article.source_article_url,
                        is_primary=True,
                    )
                    result.new += 1
                else:
                    result.rejected += 1

            except Exception as exc:
                # One bad article must never stop the rest of the feed.
                result.rejected += 1
                logger.exception("Failed to process %r", getattr(article, "headline", "?"))
                await _log_error(
                    db, source_id, sync_log_id, "store_error", str(exc),
                    {
                        "headline": getattr(article, "headline", None),
                        "source_url": getattr(article, "source_article_url", None),
                    },
                )

    result.dedup_detail = dedup.summary()

    status = (
        "success" if result.errors == 0
        else ("partial" if result.new > 0 else "failed")
    )
    await _finish_sync_log(db, sync_log_id, status, result)

    # Record the outcome on the source row so the admin surface and the
    # scheduler's health check can see it without a join.
    try:
        await db_call(
            lambda: db.table("sources")
            .update(
                {
                    "last_success_at": (
                        datetime.now(timezone.utc).isoformat()
                        if status != "failed"
                        else source_row.get("last_success_at")
                    ),
                    "consecutive_failures": (
                        0 if status != "failed" else (source_row.get("consecutive_failures") or 0) + 1
                    ),
                }
            )
            .eq("id", source_id)
            .execute()
        )
    except Exception:
        logger.debug("Could not update sources bookkeeping for %s", source_id)

    return result