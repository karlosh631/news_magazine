"""
Orchestrates one full sync run for one source:

  fetch -> parse -> normalize -> validate -> dedupe -> categorize
    -> sanitize -> store -> log

A failure on one source never affects another.
"""

from __future__ import annotations

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


ADAPTER_REGISTRY = {
    "generic_rss": GenericRssAdapter,
}


class IngestionResult:
    def __init__(self):
        self.fetched = 0
        self.new = 0
        self.duplicates = 0
        self.rejected = 0
        self.errors = 0
        self.error_details: list[dict] = []


async def run_source_sync(
    source_row: dict,
) -> IngestionResult:

    db = get_db()
    result = IngestionResult()

    # =========================================================
    # CREATE SYNC LOG
    # =========================================================

    sync_response = (
        db.table("source_sync_logs")
        .insert(
            {
                "source_id": source_row["id"],
                "status": "running",
            }
        )
        .execute()
    )

    if not sync_response.data:
        raise RuntimeError(
            "Unable to create source sync log"
        )

    sync_log = sync_response.data[0]

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
            source_row["id"],
            sync_log["id"],
            "source_disabled",
            "Source is not enabled and allowed; skipping.",
        )

        _finish_sync_log(
            db,
            sync_log["id"],
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
            source_row["id"],
            sync_log["id"],
            "unknown_adapter",
            f"No adapter registered for key '{adapter_key}'.",
        )

        _finish_sync_log(
            db,
            sync_log["id"],
            "failed",
            result,
        )

        return result

    adapter = adapter_cls(source_row)

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
            source_row["id"],
            sync_log["id"],
            "category_load_error",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log["id"],
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
            source_row["id"],
            sync_log["id"],
            "not_permitted",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log["id"],
            "failed",
            result,
        )

        return result

    except Exception as exc:

        result.errors += 1

        logger.exception(
            "Fetch/parse error for source %s",
            source_row["id"],
        )

        _log_error(
            db,
            source_row["id"],
            sync_log["id"],
            "fetch_or_parse_error",
            str(exc),
        )

        _finish_sync_log(
            db,
            sync_log["id"],
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
                    source_row["id"],
                    sync_log["id"],
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

            if existing:

                result.duplicates += 1

                # Create source relationship.
                try:
                    (
                        db.table(
                            "article_sources"
                        )
                        .insert(
                            {
                                "article_id": existing[
                                    "id"
                                ],
                                "source_id": source_row[
                                    "id"
                                ],
                                "source_article_url": (
                                    article.source_article_url
                                ),
                            }
                        )
                        .execute()
                    )

                except Exception:

                    logger.debug(
                        "Article-source relationship "
                        "already exists.",
                        exc_info=True,
                    )

                continue

            # -------------------------------------------------
            # CATEGORY
            # -------------------------------------------------

            category_id = (
                categorizer.classify(
                    article.headline,
                    article.excerpt,
                )
                or source_row.get(
                    "default_category_id"
                )
            )

            # -------------------------------------------------
            # SANITIZE HEADLINE
            # -------------------------------------------------

            safe_headline = (
                sanitize_headline(
                    article.headline
                )
            )

            if not safe_headline:
                result.rejected += 1

                _log_error(
                    db,
                    source_row["id"],
                    sync_log["id"],
                    "empty_headline",
                    "Article headline became empty after sanitization.",
                    {
                        "source_url": (
                            article.source_article_url
                        ),
                    },
                )

                continue

            # -------------------------------------------------
            # SANITIZE EXCERPT
            # -------------------------------------------------

            safe_excerpt = (
                sanitize_excerpt(
                    article.excerpt
                )
            )

            # -------------------------------------------------
            # BODY
            # -------------------------------------------------

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
                # IMPORTANT:
                # Do not blindly trust feed HTML.
                #
                # If full HTML republication is ever enabled,
                # it must pass through a dedicated HTML sanitizer.
                #
                # For now we intentionally use the safe excerpt.
                body_html = build_safe_paragraph(
                    safe_excerpt
                )

            else:
                body_html = build_safe_paragraph(
                    safe_excerpt
                )

            # -------------------------------------------------
            # CONTENT HASH
            # -------------------------------------------------

            content_hash = ""

            if article.raw_metadata:
                content_hash = (
                    article.raw_metadata.get(
                        "content_hash",
                        ""
                    )
                )

            # -------------------------------------------------
            # SLUG
            # -------------------------------------------------

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
                    # Extremely unlikely, but prevents
                    # an empty slug from reaching Supabase.
                    slug = (
                        f"article-{abs(hash(article.source_article_url))}"
                    )

            # -------------------------------------------------
            # ARTICLE ROW
            # -------------------------------------------------

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

                "source_id": source_row[
                    "id"
                ],

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

                "content_hash": content_hash,

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

            (
                db.table("article_sources")
                .insert(
                    {
                        "article_id": inserted[
                            "id"
                        ],
                        "source_id": source_row[
                            "id"
                        ],
                        "source_article_url": (
                            article.source_article_url
                        ),
                        "is_primary": True,
                    }
                )
                .execute()
            )

            result.new += 1

        except Exception as exc:

            # One broken article must never stop
            # the entire feed.

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
                source_row["id"],
                sync_log["id"],
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
        sync_log["id"],
        status,
        result,
    )

    return result


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
            db.table("source_errors")
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

        # Logging failure must never crash ingestion.
        logger.exception(
            "Failed to write source error to database"
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
            db.table("source_sync_logs")
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
