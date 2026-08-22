"""
Orchestrates one full sync run for one source:

  fetch -> parse -> normalize -> validate -> dedupe -> categorize
    -> sanitize -> store -> log

A failure on one source never affects another (the caller in
workers/scheduler.py iterates sources independently and catches
exceptions per-source).
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
    # Future source-specific adapters register here once an admin has
    # verified robots.txt/ToS and a real feed/API for that outlet.
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

    sync_log = (
        db.table("source_sync_logs")
        .insert({"source_id": source_row["id"], "status": "running"})
        .execute()
        .data[0]
    )

    if not source_row.get("enabled") or not source_row.get("allowed"):
        result.errors += 1
        _log_error(db, source_row["id"], sync_log["id"], "source_disabled",
                   "Source is not enabled+allowed; skipping.")
        _finish_sync_log(db, sync_log["id"], "failed", result)
        return result

    adapter_cls = ADAPTER_REGISTRY.get(source_row["adapter_key"])
    if adapter_cls is None:
        result.errors += 1
        _log_error(db, source_row["id"], sync_log["id"], "unknown_adapter",
                   f"No adapter registered for key '{source_row['adapter_key']}'.")
        _finish_sync_log(db, sync_log["id"], "failed", result)
        return result

    adapter = adapter_cls(source_row)
    dedupe = DuplicateDetector(db)

    categories = db.table("categories").select("id, slug").execute().data or []
    slug_to_id = {c["slug"]: c["id"] for c in categories}
    categorizer = Categorizer(slug_to_id)

    try:
        raw = await adapter.fetch()
        entries = adapter.parse(raw)
        result.fetched = len(entries)
        normalized = adapter.normalize(entries)
        normalized = adapter.validate(normalized)
    except PermissionError as exc:
        result.errors += 1
        _log_error(db, source_row["id"], sync_log["id"], "not_permitted", str(exc))
        _finish_sync_log(db, sync_log["id"], "failed", result)
        return result
    except Exception as exc:  # noqa: BLE001 — isolate this source's failure
        result.errors += 1
        _log_error(db, source_row["id"], sync_log["id"], "fetch_or_parse_error", str(exc))
        _finish_sync_log(db, sync_log["id"], "failed", result)
        return result

    for article in normalized:
        try:
            existing = dedupe.find_existing(article)
            if existing:
                result.duplicates += 1
                db.table("article_sources").insert({
                    "article_id": existing["id"],
                    "source_id": source_row["id"],
                    "source_article_url": article.source_article_url,
                }).execute()
                continue

           category_id = categorizer.classify(article.headline, article.excerpt) \
                or source_row.get("default_category_id")

            row = {
                "slug": _slugify(article.headline, article.raw_metadata.get("content_hash", "")),
                "headline": sanitize_plain_text(article.headline),
                "language": article.language,
                "status": "published",  # Set to published so articles appear instantly
                "body_html": article.body_html if hasattr(article, "body_html") and article.body_html else f"<p>{sanitize_plain_text(article.excerpt)}</p>",
                "excerpt": sanitize_plain_text(article.excerpt),
                "featured_image_url": article.thumbnail_url,
                "source_id": source_row["id"],
                "source_article_url": article.source_article_url,
                "source_name_snapshot": source_row["name"],
                "primary_category_id": category_id,
                "canonical_url": article.canonical_url,
                "title_normalized": article.raw_metadata.get("title_normalized"),
                "content_hash": article.raw_metadata.get("content_hash"),
                "published_at": article.published_at,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            inserted = db.table("articles").insert(row).execute().data[0]
            db.table("article_sources").insert({
                "article_id": inserted["id"],
                "source_id": source_row["id"],
                "source_article_url": article.source_article_url,
                "is_primary": True,
            }).execute()
            result.new += 1
        except Exception as exc:  # noqa: BLE001 — one bad article shouldn't kill the run
            result.rejected += 1
            _log_error(db, source_row["id"], sync_log["id"], "store_error", str(exc),
                       {"headline": article.headline})

    status = "success" if result.errors == 0 else "partial"
    _finish_sync_log(db, sync_log["id"], status, result)
    return result


def _slugify(headline: str, hash_suffix: str) -> str:
    import re
    base = re.sub(r"[^a-zA-Z0-9\s-]", "", headline).strip().lower()
    base = re.sub(r"\s+", "-", base)[:80]
    return f"{base}-{hash_suffix[:8]}" if hash_suffix else base


def _log_error(db, source_id, sync_log_id, error_type, message, context=None):
    logger.error("[%s] %s: %s", source_id, error_type, message)
    db.table("source_errors").insert({
        "source_id": source_id,
        "sync_log_id": sync_log_id,
        "error_type": error_type,
        "message": message,
        "context": context,
    }).execute()


def _finish_sync_log(db, sync_log_id, status, result: IngestionResult):
    db.table("source_sync_logs").update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "fetched_count": result.fetched,
        "new_count": result.new,
        "duplicate_count": result.duplicates,
        "rejected_count": result.rejected,
        "error_count": result.errors,
    }).eq("id", sync_log_id).execute()
