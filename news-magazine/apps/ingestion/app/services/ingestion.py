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
    excerpt_without_points,
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

# Attempts per database call, including the first.
DB_RETRY_ATTEMPTS = 4

# Connection-level failures. These are the exception types supabase-py /
# httpx / httpcore raise when the socket dies mid-request, as opposed to
# the server answering with an error.
_TRANSIENT_DB_ERRORS = frozenset(
    {
        "RemoteProtocolError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ReadError",
        "WriteError",
        "ClosedResourceError",
        "ProxyError",
        "LocalProtocolError",
        "IncompleteRead",
        "ServerDisconnected",
        "APIConnectionError",
    }
)

# Some of those arrive wrapped, and some surface only as text.
_TRANSIENT_DB_ERROR_MARKERS = (
    "server disconnected",
    "connection reset",
    "connection closed",
    "connection was closed",
    "remote protocol error",
    "incomplete read",
    "broken pipe",
    "timed out",
    "timeout",
    "connection refused",
    "eof occurred",
)


async def db_call(fn, *args, **kwargs):
    """
    Run a blocking supabase-py call off the event loop, with a retry.

    supabase-py has no retry of its own, and PostgREST over HTTP/2 drops
    connections routinely on a busy or idle connection pool:

        httpcore.RemoteProtocolError: Server disconnected

    That reached the pipeline as a failed lookup and then a failed
    upsert, so a single transient disconnect threw away a real article
    ("articles upsert failed for '...' : Server disconnected") while
    everything around it kept going.

    Only transport-level failures are retried. A 4xx is a real answer —
    a bad request or a constraint violation will fail identically on the
    second attempt — and retrying it just multiplies the noise. The
    operations here are reads and id-based upserts, both idempotent, so
    a retry cannot duplicate anything.
    """
    loop = asyncio.get_running_loop()

    for attempt in range(DB_RETRY_ATTEMPTS):
        try:
            return await loop.run_in_executor(
                DB_EXECUTOR, lambda: fn(*args, **kwargs)
            )
        except Exception as exc:
            if attempt == DB_RETRY_ATTEMPTS - 1 or not _is_transient_db_error(exc):
                raise
            # 0.4s, 1.2s, 3.6s — long enough for the pool to recycle the
            # dead connection, short enough not to stall a source poll.
            delay = 0.4 * (3 ** attempt)
            logger.warning(
                "Transient database error (%s); retry %s/%s in %ss.",
                type(exc).__name__, attempt + 1, DB_RETRY_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)


def _is_transient_db_error(exc: BaseException) -> bool:
    """
    True only for failures where the identical retry might succeed.

    Deliberately narrow: a unique-violation or a not-null error is a
    statement about the data, not about the connection, and retrying it
    three times just delays the real error.
    """
    name = type(exc).__name__
    if name in _TRANSIENT_DB_ERRORS:
        return True

    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_DB_ERROR_MARKERS)


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
    links_skipped: int = 0
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
            "links_skipped": self.links_skipped,
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

# articles.id, articles.source_id and article_sources.article_id are all
# uuid columns. Postgres will not coerce anything else, so a bad value
# comes back as "invalid input syntax for type uuid" — which, before
# this guard, was raised once per article and buried genuine failures.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _clean_uuid(value: Any) -> str | None:
    """The value as a bare UUID string, or None if it is not one."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _UUID_RE.match(candidate) else None


def _is_uuid(value: Any) -> bool:
    """True only for a real UUID string. None, "", and dicts all fail."""
    return _clean_uuid(value) is not None


def _extract_id(response: Any) -> str | None:
    """Pull the id out of a PostgREST response, or None if there isn't one."""
    data = getattr(response, "data", None) or []
    if not data or not isinstance(data[0], dict):
        return None
    return _clean_uuid(data[0].get("id"))


async def _lookup_id_by_slug(db, slug: str) -> str | None:
    """
    Recover the id of a row we know was written.

    PostgREST is allowed to answer an upsert with no representation
    (`Prefer: return=minimal`, or simply a response shape that carries
    no rows). The article is in the table either way, so treating an
    empty response as "not stored" counted real articles as rejected and
    permanently skipped their article_sources link.
    """
    try:
        response = await db_call(
            lambda: db.table("articles").select("id").eq("slug", slug).limit(1).execute()
        )
    except Exception:
        logger.debug("slug recovery lookup failed for %s", slug)
        return None

    for row in response.data or []:
        found = (row or {}).get("id")
        if _is_uuid(found):
            return found
    return None


async def _upsert_article_source(
    db, article_id, source_id, source_article_url, is_primary=False
) -> str:
    """
    Record that this outlet also carried the story.

    Returns "created", "exists" or "skipped", so the caller can tell a
    genuinely new outlet from one we have already recorded. That
    distinction is what keeps `source_count` honest: bumping it on every
    poll turned "reported by 4 outlets" into a number that climbed
    every few minutes for as long as the story stayed inside the dedup
    window.

    Deliberately read-then-insert rather than an ON CONFLICT upsert.
    Migration 0009 declares TWO unique indexes, and both have to be
    checked because ON CONFLICT can only ever name one:

      * (article_id, source_id)      — this outlet already carried this
                                       story; nothing to do.
      * (source_id, source_article_url) — this outlet's own link is
                                       already attached to a DIFFERENT
                                       article row.

    The second case is what produced
        duplicate key value violates unique constraint
        "article_sources_source_url_unique_idx"
    whenever an outlet re-filed a link that a previous sync had already
    attached elsewhere — for Setopati that fired repeatedly. The link
    exists either way; this story simply is not the one holding it. So
    it is reported as "exists" rather than retried or logged as noise.

    Only ever called with an id that already exists in `articles`. Both
    ids are shape-checked first: a link cannot be written without an
    article row, so when there is no valid id the call is skipped
    quietly instead of raising a raw Postgres type error.
    """
    article_uuid = _clean_uuid(article_id)
    if not article_uuid:
        if article_id:
            logger.debug(
                "article_sources: skipping link, article_id %r is not a uuid",
                article_id,
            )
        return "skipped"

    source_uuid = _clean_uuid(source_id)
    if not source_uuid:
        if source_id:
            logger.debug(
                "article_sources: skipping link, source_id %r is not a uuid",
                source_id,
            )
        return "skipped"

    if not source_article_url:
        return "skipped"

    try:
        # This outlet already carried this story.
        existing = await db_call(
            lambda: db.table("article_sources")
            .select("id")
            .eq("article_id", article_uuid)
            .eq("source_id", source_uuid)
            .limit(1)
            .execute()
        )
        if existing.data:
            return "exists"

        # The same outlet link is already attached to another article.
        # Nothing to write, and it is not an error.
        linked_elsewhere = await db_call(
            lambda: db.table("article_sources")
            .select("id, article_id")
            .eq("source_id", source_uuid)
            .eq("source_article_url", source_article_url)
            .limit(1)
            .execute()
        )
        if linked_elsewhere.data:
            return "exists"

        await db_call(
            lambda: db.table("article_sources")
            .insert(
                {
                    "article_id": article_uuid,
                    "source_id": source_uuid,
                    "source_article_url": source_article_url,
                    "is_primary": is_primary,
                }
            )
            .execute()
        )
        return "created"
    except Exception as exc:
        # A foreign-key failure here is benign and expected: the article
        # was matched as a duplicate, then the TTL prune deleted it
        # before the link could be written. The story is already gone,
        # so there is nothing to link. Logging it as a warning produced
        # a wall of noise on every source that ran while a prune was in
        # flight.
        if "article_sources_article_id_fkey" in str(exc) or "23503" in str(exc):
            logger.debug(
                "article_sources: article %s was pruned before the link was written",
                article_uuid,
            )
            return "skipped"

        # Anything else is a degraded merge record, not a lost story — the
        # article itself is already stored.
        logger.warning(
            "Could not record article_sources link for article %s: %s",
            article_uuid, exc,
        )
        return "skipped"


async def _bump_source_count(db, article_id: str) -> None:
    """
    Another outlet carried this story; increment the counter.

    Uses an atomic SQL increment rather than read-then-write: two
    sources polled concurrently would otherwise both read 1, both write
    2, and one increment would be lost.
    """
    if not _is_uuid(article_id):
        return

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

    Returns the article's uuid, or None when the row was NOT written.
    Never raises: the caller uses the return value to decide whether it
    is safe to write the article_sources link, so an exception here
    would turn a storage problem into an unhandled error mid-feed.
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
        # source_count is deliberately NOT in this payload. This is an
        # UPSERT, so on a re-poll Postgres turns it into
        # SET source_count = 1 — resetting "reported by 4 outlets" back
        # to 1 every cycle. The column is `not null default 1`
        # (migration 0007), so a fresh insert still gets 1 and an
        # existing row keeps whatever increment_article_source_count()
        # raised it to.
    }

    try:
        response = await db_call(
            lambda: db.table("articles").upsert(row, on_conflict="slug").execute()
        )
    except Exception as exc:
        # articles_content_hash_idx is UNIQUE (0001) while we upsert on
        # `slug`, so a story whose hash already exists under a different
        # slug cannot be written here. That is a legitimate outcome — the
        # story is already stored — so it is reported, not raised, and the
        # caller skips the link instead of chasing a dead article id.
        logger.info(
            "articles upsert failed for %r: %s",
            article.headline[:80], exc,
        )
        return None

    article_id = _extract_id(response)
    if article_id:
        return article_id

    # No representation came back. The row may still be in the table, so
    # look it up by the slug we just wrote rather than reporting a
    # successfully stored article as rejected.
    return await _lookup_id_by_slug(db, row["slug"])


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
                    link = await _upsert_article_source(
                        db=db,
                        article_id=verdict.existing_id,
                        source_id=source_id,
                        source_article_url=article.source_article_url,
                        is_primary=False,
                    )
                    if link == "skipped":
                        result.links_skipped += 1
                    # Only a link we had not recorded before is a new
                    # outlet. Bumping on every poll made source_count
                    # climb continuously for any story still inside the
                    # 12h dedup window.
                    if link == "created":
                        await _bump_source_count(db, verdict.existing_id)
                    logger.info(
                        # f-string rather than %-args: the worker's
                        # structlog config renders stdlib log records as
                        # Message:/Arguments: without interpolating them,
                        # so the actual headline never reached the log.
                        f"Duplicate ({verdict.reason}, {verdict.confidence:.0f}%): "
                        f"{headline!r} <- {verdict.matched_title!r}",
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
                # The excerpt and the points are both extracted from the
                # same article, so the excerpt's opening sentences are
                # normally the very sentences the points were built from.
                # Stored together they render as the same paragraph twice
                # on the article page, so the excerpt is trimmed to what
                # the points do not already say.
                store_excerpt = excerpt_without_points(
                    excerpt, summary.points if summary and summary.points else None
                )

                article_id = await store_article(
                    db=db,
                    article=article,
                    media=media,
                    category_id=category_id,
                    source_row=source_row,
                    content_hash=content_hash,
                    title_normalized=title_normalized,
                    excerpt=store_excerpt,
                    summary=summary,
                )

                if article_id:
                    # The row exists, so the link is safe to write. This
                    # is the primary outlet for a story that is already
                    # stored, so the counter must not move: it already
                    # counted this outlet the first time.
                    link = await _upsert_article_source(
                        db=db,
                        article_id=article_id,
                        source_id=source_id,
                        source_article_url=article.source_article_url,
                        is_primary=True,
                    )
                    if link == "skipped":
                        result.links_skipped += 1
                    result.new += 1

                    # Teach the deduper this article's id. Without it an
                    # in-run duplicate resolves with existing_id=None
                    # and falls through to being stored as a second copy
                    # of the same story.
                    dedup.bind_article_id(
                        headline=headline,
                        fingerprint=fingerprint,
                        url=article.canonical_url or article.source_article_url,
                        article_id=article_id,
                    )
                else:
                    # No valid id means no row. Count it as rejected and
                    # deliberately do NOT attempt the article_sources
                    # write — there is nothing to link to.
                    result.rejected += 1

            except asyncio.CancelledError:
                # A BaseException, so the handler below never catches it.
                # Swallowing it here would make a cancelled sync look
                # like a run that simply processed fewer articles, and
                # the caller would carry on believing the task is alive.
                # _close_orphaned_sync_logs() in the scheduler cleans up
                # the log row this run will not get to finish.
                raise

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