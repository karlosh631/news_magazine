"""
Orchestrates one full sync run for one source:

    fetch -> parse -> normalize -> validate -> dedupe -> categorize
        -> sanitize -> store -> log

A failure on one source never affects another.

Security principles:
- Only enabled + allowed sources are processed.
- Adapter controls whether fetching is permitted.
- RSS-provided HTML is never directly rendered as article HTML.
- Headlines and excerpts are sanitized before database storage.
- Full article HTML is only considered when republish_permission=True.
- A broken article must never stop the remaining feed.
- Database errors are logged without crashing the entire ingestion service.
- article_sources relationships are written idempotently.
- Duplicate source URLs cannot create duplicate article-source relationships.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from app.database import get_db
from app.services.categorizer import Categorizer
from app.services.duplicate_detector import DuplicateDetector
from app.services.sanitizer import (
    build_safe_paragraph,
    sanitize_excerpt,
    sanitize_headline,
)
from app.sources.generic_rss import GenericRssAdapter


logger = logging.getLogger("ingestion")


# =============================================================
# ADAPTER REGISTRY
# =============================================================

ADAPTER_REGISTRY = {
    "generic_rss": GenericRssAdapter,
}


# =============================================================
# INGESTION RESULT
# =============================================================

class IngestionResult:

    def __init__(self):

        self.fetched = 0
        self.new = 0
        self.duplicates = 0
        self.rejected = 0
        self.errors = 0

        self.error_details: list[dict] = []


# =============================================================
# MAIN SOURCE SYNC
# =============================================================

async def run_source_sync(
    source_row: dict,
) -> IngestionResult:

    db = get_db()

    result = IngestionResult()

    source_id = source_row["id"]

    # =========================================================
    # CREATE SYNC LOG
    # =========================================================

    try:

        sync_response = (
            db.table("source_sync_logs")
            .insert(
                {
                    "source_id": source_id,
                    "status": "running",
                }
            )
            .execute()
        )

    except Exception as exc:

        logger.exception(
            "Unable to create sync log for source %s",
            source_id,
        )

        result.errors += 1

        result.error_details.append(
            {
                "type": "sync_log_create_error",
                "message": str(exc),
            }
        )

        return result

    if not sync_response.data:

        raise RuntimeError(
            "Unable to create source sync log"
        )

    sync_log = sync_response.data[0]

    sync_log_id = sync_log["id"]

    # =========================================================
    # VALIDATE SOURCE
    # =========================================================

    if (
        not source_row.get("enabled")
        or not source_row.get("allowed")
    ):

        result.errors += 1

        _log_error(
            db,
            source_id,
            sync_log_id,
            "source_disabled",
            "Source is not enabled and allowed; skipping.",
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    # =========================================================
    # FIND ADAPTER
    # =========================================================

    adapter_key = source_row.get(
        "adapter_key"
    )

    adapter_cls = ADAPTER_REGISTRY.get(
        adapter_key
    )

    if adapter_cls is None:

        result.errors += 1

        _log_error(
            db,
            source_id,
            sync_log_id,
            "unknown_adapter",
            f"No adapter registered for key '{adapter_key}'.",
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    # =========================================================
    # INITIALIZE ADAPTER
    # =========================================================

    try:

        adapter = adapter_cls(
            source_row
        )

    except Exception as exc:

        result.errors += 1

        logger.exception(
            "Failed to initialize adapter for source %s",
            source_id,
        )

        _log_error(
            db,
            source_id,
            sync_log_id,
            "adapter_initialization_error",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    # =========================================================
    # DUPLICATE DETECTOR
    # =========================================================

    dedupe = DuplicateDetector(db)

    # =========================================================
    # LOAD CATEGORIES
    # =========================================================

    try:

        categories_response = (
            db.table("categories")
            .select("id, slug")
            .execute()
        )

        categories = (
            categories_response.data or []
        )

        slug_to_id = {
            category["slug"]: category["id"]
            for category in categories
            if category.get("slug")
            and category.get("id")
        }

    except Exception as exc:

        result.errors += 1

        logger.exception(
            "Failed to load categories"
        )

        _log_error(
            db,
            source_id,
            sync_log_id,
            "category_load_error",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    categorizer = Categorizer(
        slug_to_id
    )

    # =========================================================
    # FETCH -> PARSE -> NORMALIZE -> VALIDATE
    # =========================================================

    try:

        raw = await adapter.fetch()

        entries = adapter.parse(
            raw
        )

        result.fetched = len(
            entries
        )

        normalized = adapter.normalize(
            entries
        )

        normalized = adapter.validate(
            normalized
        )

    except PermissionError as exc:

        result.errors += 1

        _log_error(
            db,
            source_id,
            sync_log_id,
            "not_permitted",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    except Exception as exc:

        result.errors += 1

        logger.exception(
            "Fetch/parse error for source %s",
            source_id,
        )

        _log_error(
            db,
            source_id,
            sync_log_id,
            "fetch_or_parse_error",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log_id,
            "failed",
            result,
        )

        return result

    # =========================================================
    # PROCESS EACH ARTICLE
    # =========================================================

    for article in normalized:

        try:

            # -------------------------------------------------
            # BASIC VALIDATION
            # -------------------------------------------------

            if not article.headline:

                result.rejected += 1

                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "missing_headline",
                    "Article has no headline.",
                    {
                        "source_url": (
                            article.source_article_url
                        ),
                    },
                )

                continue

            if not article.source_article_url:

                result.rejected += 1

                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "missing_source_url",
                    "Article has no source article URL.",
                )

                continue

            # -------------------------------------------------
            # NORMALIZE SOURCE URL
            # -------------------------------------------------
            #
            # IMPORTANT:
            #
            # The exact same normalized URL must be used
            # everywhere:
            #
            #     duplicate detection
            #     article_sources
            #
            # This prevents:
            #
            #     URL?utm_source=rss
            #
            # and
            #
            #     URL
            #
            # from accidentally becoming separate relationships.
            # -------------------------------------------------

            normalized_source_url = (
                dedupe.normalize_url(
                    article.source_article_url
                )
            )

            if not normalized_source_url:

                result.rejected += 1

                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "invalid_source_url",
                    "Article has no valid normalized source URL.",
                    {
                        "headline": article.headline,
                        "source_url": (
                            article.source_article_url
                        ),
                    },
                )

                continue

            # Keep normalized URL on the article object
            # for all subsequent operations.

            article.source_article_url = (
                normalized_source_url
            )

            # -------------------------------------------------
            # NORMALIZE CANONICAL URL
            # -------------------------------------------------

            article.canonical_url = (
                dedupe.normalize_url(
                    article.canonical_url
                    or article.source_article_url
                )
            )

            if not article.canonical_url:

                result.rejected += 1

                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "invalid_canonical_url",
                    "Article has no valid canonical URL.",
                    {
                        "headline": article.headline,
                        "source_url": (
                            article.source_article_url
                        ),
                    },
                )

                continue

            # -------------------------------------------------
            # DUPLICATE DETECTION
            # -------------------------------------------------

            existing = (
                dedupe.find_existing(
                    article
                )
            )

            # =================================================
            # EXISTING ARTICLE
            # =================================================

            if existing:

                result.duplicates += 1

                # -------------------------------------------------
                # IDEMPOTENT ARTICLE-SOURCE RELATIONSHIP
                # -------------------------------------------------
                #
                # DO NOT use:
                #
                #     .insert(...)
                #
                # because the same source URL may already exist.
                #
                # Use:
                #
                #     .upsert(...)
                #
                # with:
                #
                #     on_conflict="source_id,source_article_url"
                #
                # and:
                #
                #     ignore_duplicates=True
                #
                # Therefore repeated ingestion is safe.
                # -------------------------------------------------

                relationship_ok = (
                    _upsert_article_source(
                        db=db,
                        article_id=existing["id"],
                        source_id=source_id,
                        source_article_url=(
                            article.source_article_url
                        ),
                        is_primary=False,
                    )
                )

                if not relationship_ok:

                    logger.warning(
                        "Could not create/verify article-source "
                        "relationship for duplicate article %s",
                        existing["id"],
                    )

                continue

            # =================================================
            # CATEGORY
            # =================================================

            category_id = (
                categorizer.classify(
                    article.headline,
                    article.excerpt,
                )
                or source_row.get(
                    "default_category_id"
                )
            )

            # =================================================
            # SANITIZE HEADLINE
            # =================================================

            safe_headline = (
                sanitize_headline(
                    article.headline
                )
            )

            if not safe_headline:

                result.rejected += 1

                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "empty_headline",
                    "Article headline became empty after sanitization.",
                    {
                        "source_url": (
                            article.source_article_url
                        ),
                    },
                )

                continue

            # =================================================
            # SANITIZE EXCERPT
            # =================================================

            safe_excerpt = (
                sanitize_excerpt(
                    article.excerpt
                )
            )

            # =================================================
            # BODY
            # =================================================

            if (
                getattr(
                    article,
                    "full_body_html",
                    None,
                )
                and source_row.get(
                    "republish_permission",
                    False,
                )
            ):

                # -------------------------------------------------
                # IMPORTANT SECURITY RULE
                # -------------------------------------------------
                #
                # Do not trust full_body_html until a dedicated
                # HTML sanitizer is implemented.
                #
                # Even when republish_permission=True, RSS HTML
                # should NOT be inserted directly.
                # -------------------------------------------------

                body_html = (
                    build_safe_paragraph(
                        safe_excerpt
                    )
                )

            else:

                body_html = (
                    build_safe_paragraph(
                        safe_excerpt
                    )
                )

            # =================================================
            # CONTENT HASH
            # =================================================

            content_hash = ""

            if article.raw_metadata:

                content_hash = (
                    article.raw_metadata.get(
                        "content_hash",
                        "",
                    )
                    or ""
                )

            # =================================================
            # SLUG
            # =================================================

            slug = _slugify(
                safe_headline,
                content_hash,
            )

            # -------------------------------------------------
            # FALLBACK SLUG
            # -------------------------------------------------

            if not slug:

                if content_hash:

                    slug = (
                        f"article-{content_hash[:12]}"
                    )

                else:

                    source_url_hash = (
                        hashlib.sha256(
                            article.source_article_url.encode(
                                "utf-8"
                            )
                        )
                        .hexdigest()[:12]
                    )

                    slug = (
                        f"article-{source_url_hash}"
                    )

            # =================================================
            # ARTICLE ROW
            # =================================================

            row = {

                "slug": slug,

                "headline": safe_headline,

                "language": (
                    article.language
                    or source_row.get(
                        "default_language",
                        "ne",
                    )
                ),

                "status": "published",

                "body_html": body_html,

                "excerpt": safe_excerpt,

                "featured_image_url": (
                    article.thumbnail_url
                ),

                "source_id": source_id,

                "source_article_url": (
                    article.source_article_url
                ),

                "source_name_snapshot": (
                    source_row.get(
                        "name",
                        "Unknown Source",
                    )
                ),

                "primary_category_id": (
                    category_id
                ),

                "canonical_url": (
                    article.canonical_url
                ),

                "title_normalized": (
                    article.raw_metadata.get(
                        "title_normalized"
                    )
                    if article.raw_metadata
                    else None
                ),

                "content_hash": (
                    content_hash
                ),

                "published_at": (
                    article.published_at
                ),

                "ingested_at": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            }

            # =================================================
            # INSERT ARTICLE
            # =================================================

            insert_response = (
                db.table("articles")
                .insert(row)
                .execute()
            )

            if not insert_response.data:

                raise RuntimeError(
                    "Article insert returned no data"
                )

            inserted = (
                insert_response.data[0]
            )

            # =================================================
            # LINK ARTICLE TO SOURCE
            # =================================================
            #
            # IMPORTANT:
            #
            # This is now idempotent.
            #
            # If the relationship already exists:
            #
            #     no duplicate row is created
            #
            # If it does not exist:
            #
            #     it is inserted.
            # =================================================

            relationship_ok = (
                _upsert_article_source(
                    db=db,
                    article_id=inserted["id"],
                    source_id=source_id,
                    source_article_url=(
                        article.source_article_url
                    ),
                    is_primary=True,
                )
            )

            if not relationship_ok:

                # The article itself was successfully inserted,
                # but its source relationship failed.
                #
                # Do not count it as a successful new article
                # because the relationship is essential.

                raise RuntimeError(
                    "Article inserted but article-source "
                    "relationship could not be created"
                )

            result.new += 1

        except Exception as exc:

            # -------------------------------------------------
            # ONE BROKEN ARTICLE MUST NOT STOP THE FEED
            # -------------------------------------------------

            result.rejected += 1

            logger.exception(
                "Failed to store article: %s",
                getattr(
                    article,
                    "headline",
                    "unknown article",
                ),
            )

            _log_error(
                db,
                source_id,
                sync_log_id,
                "store_error",
                str(exc),
                {
                    "headline": getattr(
                        article,
                        "headline",
                        None,
                    ),
                    "source_url": getattr(
                        article,
                        "source_article_url",
                        None,
                    ),
                },
            )

    # =========================================================
    # FINISH
    # =========================================================

    if result.errors == 0:

        status = "success"

    elif result.new > 0:

        status = "partial"

    else:

        status = "failed"

    _finish_sync_log(
        db,
        sync_log_id,
        status,
        result,
    )

    return result


# =============================================================
# ARTICLE-SOURCE UPSERT
# =============================================================

def _upsert_article_source(
    db,
    article_id,
    source_id,
    source_article_url,
    is_primary=False,
) -> bool:
    """
    Create an article_sources relationship safely.

    The database must have a unique constraint/index on:

        (source_id, source_article_url)

    Re-running ingestion with the same source URL will therefore
    not create another relationship.

    Existing relationship:
        ignored

    New relationship:
        inserted
    """

    if not article_id:
        logger.error(
            "article_id is missing while creating article_sources"
        )
        return False

    if not source_id:
        logger.error(
            "source_id is missing while creating article_sources"
        )
        return False

    if not source_article_url:
        logger.error(
            "source_article_url is missing while creating article_sources"
        )
        return False

    try:

        response = (
            db.table("article_sources")
            .upsert(
                {
                    "article_id": article_id,
                    "source_id": source_id,
                    "source_article_url": source_article_url,
                    "is_primary": is_primary,
                },
                on_conflict=(
                    "source_id,source_article_url"
                ),
                ignore_duplicates=True,
            )
            .execute()
        )

        # With ignore_duplicates=True, Supabase/PostgREST may return
        # an empty data array when the row already exists.
        #
        # That is NOT an error.
        return True

    except Exception as exc:

        logger.exception(
            "Failed to upsert article-source relationship "
            "(source_id=%s, url=%s)",
            source_id,
            source_article_url,
        )

        return False


# =============================================================
# SLUG GENERATOR
# =============================================================

def _slugify(
    headline: str,
    hash_suffix: str,
) -> str:

    headline = headline or ""

    # Keep ASCII letters/numbers for URL compatibility.
    base = re.sub(
        r"[^a-zA-Z0-9\s-]",
        "",
        headline,
    )

    base = base.strip().lower()

    base = re.sub(
        r"\s+",
        "-",
        base,
    )

    base = re.sub(
        r"-+",
        "-",
        base,
    )

    base = base[:80].strip("-")

    if hash_suffix:

        return (
            f"{base}-{hash_suffix[:8]}"
            if base
            else f"article-{hash_suffix[:8]}"
        )

    return base


# =============================================================
# ERROR LOGGING
# =============================================================

def _log_error(
    db,
    source_id,
    sync_log_id,
    error_type,
    message,
    context=None,
):

    logger.error(
        "[%s] %s: %s",
        source_id,
        error_type,
        message,
    )

    try:

        (
            db.table(
                "source_errors"
            )
            .insert(
                {
                    "source_id": source_id,

                    "sync_log_id": sync_log_id,

                    "error_type": error_type,

                    "message": str(
                        message
                    )[:5000],

                    "context": context,
                }
            )
            .execute()
        )

    except Exception:

        # Logging failure must never crash ingestion.
        logger.exception(
            "Failed to write source error "
            "to database"
        )


# =============================================================
# FINISH SYNC LOG
# =============================================================

def _finish_sync_log(
    db,
    sync_log_id,
    status,
    result: IngestionResult,
):

    try:

        (
            db.table(
                "source_sync_logs"
            )
            .update(
                {
                    "finished_at": (
                        datetime.now(
                            timezone.utc
                        ).isoformat()
                    ),

                    "status": status,

                    "fetched_count": (
                        result.fetched
                    ),

                    "new_count": (
                        result.new
                    ),

                    "duplicate_count": (
                        result.duplicates
                    ),

                    "rejected_count": (
                        result.rejected
                    ),

                    "error_count": (
                        result.errors
                    ),
                }
            )
            .eq(
                "id",
                sync_log_id,
            )
            .execute()
        )

    except Exception:

        logger.exception(
            "Failed to update sync log %s",
            sync_log_id,
        )
