"""
Orchestrates one full sync run for one source:

  fetch -> parse -> normalize -> validate -> dedupe -> categorize
    -> sanitize -> store -> log

A failure on one source never affects another.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.database import get_db
from app.services.categorizer import Categorizer
from app.services.duplicate_detector import DuplicateDetector
from app.services.sanitizer import sanitize_plain_text
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


async def run_source_sync(source_row: dict) -> IngestionResult:
    db = get_db()
    result = IngestionResult()

    # ---------------------------------------------------------
    # Create sync log
    # ---------------------------------------------------------
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
        raise RuntimeError("Unable to create source sync log")

    sync_log = sync_response.data[0]

    # ---------------------------------------------------------
    # Validate source
    # ---------------------------------------------------------
    if not source_row.get("enabled") or not source_row.get("allowed"):
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

    # ---------------------------------------------------------
    # Find adapter
    # ---------------------------------------------------------
    adapter_key = source_row.get("adapter_key")

    adapter_cls = ADAPTER_REGISTRY.get(adapter_key)

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

    # ---------------------------------------------------------
    # Load categories
    # ---------------------------------------------------------
    categories_response = (
        db.table("categories")
        .select("id, slug")
        .execute()
    )

    categories = categories_response.data or []

    slug_to_id = {
        category["slug"]: category["id"]
        for category in categories
    }

    categorizer = Categorizer(slug_to_id)

    # ---------------------------------------------------------
    # FETCH -> PARSE -> NORMALIZE -> VALIDATE
    # ---------------------------------------------------------
    try:
        raw = await adapter.fetch()

        entries = adapter.parse(raw)

        result.fetched = len(entries)

        normalized = adapter.normalize(entries)

        normalized = adapter.validate(normalized)

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

    # ---------------------------------------------------------
    # PROCESS EACH ARTICLE
    # ---------------------------------------------------------
    for article in normalized:

        try:
            # -------------------------------------------------
            # Duplicate detection
            # -------------------------------------------------
            existing = dedupe.find_existing(article)

            if existing:
                result.duplicates += 1

                # Don't allow one duplicate relationship to crash
                # the entire synchronization.
                try:
                    db.table("article_sources").insert(
                        {
                            "article_id": existing["id"],
                            "source_id": source_row["id"],
                            "source_article_url": article.source_article_url,
                        }
                    ).execute()
                except Exception:
                    logger.debug(
                        "Article-source relationship already exists",
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
                or source_row.get("default_category_id")
            )

            # -------------------------------------------------
            # BODY
            # -------------------------------------------------
            if (
                hasattr(article, "body_html")
                and article.body_html
            ):
                body_html = article.body_html
            else:
                safe_excerpt = sanitize_plain_text(
                    article.excerpt or ""
                )

                body_html = f"<p>{safe_excerpt}</p>"

            # -------------------------------------------------
            # SLUG
            # -------------------------------------------------
            content_hash = (
                article.raw_metadata.get("content_hash", "")
                if article.raw_metadata
                else ""
            )

            slug = _slugify(
                article.headline,
                content_hash,
            )

            # -------------------------------------------------
            # ARTICLE ROW
            # -------------------------------------------------
            row = {
                "slug": slug,

                "headline": sanitize_plain_text(
                    article.headline
                ),

                "language": article.language,

                # Published immediately after successful ingestion.
                "status": "published",

                "body_html": body_html,

                "excerpt": sanitize_plain_text(
                    article.excerpt or ""
                ),

                "featured_image_url": article.thumbnail_url,

                "source_id": source_row["id"],

                "source_article_url": article.source_article_url,

                "source_name_snapshot": source_row["name"],

                "primary_category_id": category_id,

                "canonical_url": article.canonical_url,

                "title_normalized": (
                    article.raw_metadata.get(
                        "title_normalized"
                    )
                    if article.raw_metadata
                    else None
                ),

                "content_hash": content_hash,

                "published_at": article.published_at,

                "ingested_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            # -------------------------------------------------
            # INSERT ARTICLE
            # -------------------------------------------------
            insert_response = (
                db.table("articles")
                .insert(row)
                .execute()
            )

            if not insert_response.data:
                raise RuntimeError(
                    "Article insert returned no data"
                )

            inserted = insert_response.data[0]

            # -------------------------------------------------
            # LINK ARTICLE TO SOURCE
            # -------------------------------------------------
            db.table("article_sources").insert(
                {
                    "article_id": inserted["id"],
                    "source_id": source_row["id"],
                    "source_article_url": article.source_article_url,
                    "is_primary": True,
                }
            ).execute()

            result.new += 1

        except Exception as exc:
            # One broken article must never stop the entire feed.
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

    # ---------------------------------------------------------
    # FINISH
    # ---------------------------------------------------------
    status = (
        "success"
        if result.errors == 0
        else "partial"
    )

    _finish_sync_log(
        db,
        sync_log["id"],
        status,
        result,
    )

    return result


def _slugify(
    headline: str,
    hash_suffix: str,
) -> str:

    import re

    headline = headline or ""

    base = re.sub(
        r"[^a-zA-Z0-9\s-]",
        "",
        headline,
    ).strip().lower()

    base = re.sub(
        r"\s+",
        "-",
        base,
    )

    base = base[:80].strip("-")

    if hash_suffix:
        return f"{base}-{hash_suffix[:8]}"

    return base


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
                    "message": message,
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
                    "finished_at": datetime.now(
                        timezone.utc
                    ).isoformat(),

                    "status": status,

                    "fetched_count": result.fetched,

                    "new_count": result.new,

                    "duplicate_count": result.duplicates,

                    "rejected_count": result.rejected,

                    "error_count": result.errors,
                }
            )
            .eq("id", sync_log_id)
            .execute()
        )

    except Exception:
        logger.exception(
            "Failed to update sync log %s",
            sync_log_id,
        )
