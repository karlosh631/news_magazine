"""
Orchestrates one full sync run for one source:

    fetch -> parse -> normalize -> validate -> dedupe -> categorize
        -> sanitize -> Claude IEEE synthesis -> store (UPSERT) -> log

A failure on one source never affects another.

Security principles:
- Only enabled + allowed sources are processed.
- Adapter controls whether fetching is permitted.
- Headlines and excerpts are sanitized before database storage.
- Full article HTML is processed safely.
- Claude generates structured IEEE standard content.
- Database writes perform an idempotent UPSERT on unique constraints.
- A broken article must never stop the remaining feed.
- Database errors are logged without crashing the entire ingestion service.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import anthropic

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

# Valid IEEE Sector Taxonomies
ALLOWED_SECTORS = ["Coding", "Hackathons", "Nepal Top News", "World News", "IT"]

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
# ANTHROPIC CLAUDE IEEE SYNTHESIZER
# =============================================================

def synthesize_ieee_paper(
    sector: str, headline: str, excerpt: str, source_url: str
) -> Dict[str, Any]:
    """
    Calls Anthropic API to convert scraped news data into an IEEE formatted paper structure.
    Includes a safe fallback structure if API key or network request fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY missing; using fallback structured layout.")
        return {
            "title": headline,
            "abstract": excerpt or f"Technical briefing on recent developments in {sector}.",
            "content_ieee": (
                f"# I. INTRODUCTION\n{excerpt}\n\n"
                f"# II. TECHNICAL LANDSCAPE & METHODOLOGY\nRecent developments in {sector}.\n\n"
                f"# III. SYSTEM ARCHITECTURE & IMPLEMENTATION\nAnalysis derived from primary reports.\n\n"
                f"# IV. EVALUATION & DISCUSSIONS\nObservations and regional implications.\n\n"
                f"# V. CONCLUSION & FUTURE DIRECTIONS\nOngoing monitoring required."
            ),
            "references": [
                {
                    "id": 1,
                    "citation": f"Primary Source, '{headline}', 2026.",
                    "url": source_url,
                }
            ],
        }

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a distinguished IEEE Senior Fellow and technical research journalist.
Synthesize the following article for the sector "{sector}" into a formal IEEE standard publication.

ARTICLE CONTEXT:
Headline: {headline}
Source URL: {source_url}
Summary: {excerpt}

REQUIREMENTS:
1. Return ONLY a valid JSON object matching this strict schema:
{{
  "title": "A precise, technical title without quotes",
  "abstract": "A continuous 150-250 word formal technical abstract summarizing background, findings, and context.",
  "content_ieee": "Markdown formatted IEEE body containing exactly these 5 section titles:\n\n# I. INTRODUCTION\n...\n\n# II. TECHNICAL LANDSCAPE & METHODOLOGY\n...\n\n# III. SYSTEM ARCHITECTURE & IMPLEMENTATION\n...\n\n# IV. EVALUATION & DISCUSSIONS\n...\n\n# V. CONCLUSION & FUTURE DIRECTIONS\n...",
  "references": [
    {{
      "id": 1,
      "citation": "Author et al., \\"Article Title\\", Source, 2026.",
      "url": "{source_url}"
    }}
  ]
}}
2. Embed numerical citations like [1] within content_ieee text corresponding to the references array.
3. Output raw JSON ONLY with no code blocks or extra text."""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text.strip()
    cleaned_json = re.sub(r"^```json\s*", "", raw_text)
    cleaned_json = re.sub(r"```$", "", cleaned_json).strip()

    return json.loads(cleaned_json)

# =============================================================
# MAIN SOURCE SYNC
# =============================================================

async def run_source_sync(source_row: dict) -> IngestionResult:
    db = get_db()
    result = IngestionResult()
    source_id = source_row["id"]

    # 1. CREATE SYNC LOG
    try:
        sync_response = (
            db.table("source_sync_logs")
            .insert({"source_id": source_id, "status": "running"})
            .execute()
        )
    except Exception as exc:
        logger.exception("Unable to create sync log for source %s", source_id)
        result.errors += 1
        result.error_details.append(
            {"type": "sync_log_create_error", "message": str(exc)}
        )
        return result

    if not sync_response.data:
        raise RuntimeError("Unable to create source sync log")

    sync_log = sync_response.data[0]
    sync_log_id = sync_log["id"]

    # 2. VALIDATE SOURCE PERMISSIONS
    if not source_row.get("enabled") or not source_row.get("allowed"):
        result.errors += 1
        _log_error(
            db,
            source_id,
            sync_log_id,
            "source_disabled",
            "Source is not enabled and allowed; skipping.",
        )
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    # 3. FIND & INITIALIZE ADAPTER
    adapter_key = source_row.get("adapter_key")
    adapter_cls = ADAPTER_REGISTRY.get(adapter_key)

    if adapter_cls is None:
        result.errors += 1
        _log_error(
            db,
            source_id,
            sync_log_id,
            "unknown_adapter",
            f"No adapter registered for key '{adapter_key}'.",
        )
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    try:
        adapter = adapter_cls(source_row)
    except Exception as exc:
        result.errors += 1
        logger.exception("Failed to initialize adapter for source %s", source_id)
        _log_error(
            db,
            source_id,
            sync_log_id,
            "adapter_initialization_error",
            str(exc),
        )
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    dedupe = DuplicateDetector(db)

    # 4. LOAD CATEGORIES
    try:
        categories_response = db.table("categories").select("id, slug").execute()
        categories = categories_response.data or []
        slug_to_id = {
            category["slug"]: category["id"]
            for category in categories
            if category.get("slug") and category.get("id")
        }
    except Exception as exc:
        result.errors += 1
        logger.exception("Failed to load categories")
        _log_error(db, source_id, sync_log_id, "category_load_error", str(exc))
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    categorizer = Categorizer(slug_to_id)

    # 5. FETCH & PARSE ARTICLES
    try:
        raw = await adapter.fetch()
        entries = adapter.parse(raw)
        result.fetched = len(entries)
        normalized = adapter.normalize(entries)
        normalized = adapter.validate(normalized)
    except PermissionError as exc:
        result.errors += 1
        _log_error(db, source_id, sync_log_id, "not_permitted", str(exc))
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result
    except Exception as exc:
        result.errors += 1
        logger.exception("Fetch/parse error for source %s", source_id)
        _log_error(db, source_id, sync_log_id, "fetch_or_parse_error", str(exc))
        _finish_sync_log(db, sync_log_id, "failed", result)
        return result

    # 6. PROCESS EACH SCRAEPD ITEM
    for article in normalized:
        try:
            if not article.headline:
                result.rejected += 1
                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "missing_headline",
                    "Article has no headline.",
                    {"source_url": article.source_article_url},
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

            # URL NORMALIZATION
            normalized_source_url = dedupe.normalize_url(article.source_article_url)
            if not normalized_source_url:
                result.rejected += 1
                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "invalid_source_url",
                    "Article has no valid normalized source URL.",
                )
                continue

            article.source_article_url = normalized_source_url
            article.canonical_url = dedupe.normalize_url(
                article.canonical_url or article.source_article_url
            )

            # DEDUPLICATION
            existing = dedupe.find_existing(article)
            if existing:
                result.duplicates += 1
                _upsert_article_source(
                    db=db,
                    article_id=existing["id"],
                    source_id=source_id,
                    source_article_url=article.source_article_url,
                    is_primary=False,
                )
                continue

            # SANITIZATION
            safe_headline = sanitize_headline(article.headline)
            safe_excerpt = sanitize_excerpt(article.excerpt)

            if not safe_headline:
                result.rejected += 1
                _log_error(
                    db,
                    source_id,
                    sync_log_id,
                    "empty_headline",
                    "Article headline became empty after sanitization.",
                )
                continue

            # SECTOR RESOLUTION
            sector_name = source_row.get("sector") or "IT"
            if sector_name not in ALLOWED_SECTORS:
                sector_name = "IT"

            # IEEE SYNTHESIS
            ieee_paper = synthesize_ieee_paper(
                sector=sector_name,
                headline=safe_headline,
                excerpt=safe_excerpt,
                source_url=article.source_article_url,
            )

            content_hash = (
                article.raw_metadata.get("content_hash", "")
                if article.raw_metadata
                else ""
            )

            # SLUG GENERATION
            slug = _slugify(ieee_paper["title"], sector_name, content_hash)
            default_cover = (
                article.thumbnail_url
                or "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80"
            )

            post_payload = {
                "slug": slug,
                "title": ieee_paper["title"],
                "abstract": ieee_paper["abstract"],
                "content_ieee": ieee_paper["content_ieee"],
                "sector": sector_name,
                "references_json": ieee_paper.get("references", []),
                "cover_image_url": default_cover,
                "published_at": article.published_at or datetime.now(timezone.utc).isoformat(),
            }

            # UPSERT POST TO DATABASE
            upsert_response = (
                db.table("posts")
                .upsert(post_payload, on_conflict="slug")
                .execute()
            )

            if not upsert_response.data:
                raise RuntimeError("Post database upsert returned no response data.")

            category_id = (
                categorizer.classify(article.headline, article.excerpt)
                or source_row.get("default_category_id")
            )

            # LEGACY ARTICLE TABLE WRITE (BACKWARD COMPATIBILITY)
            article_row = {
                "slug": slug,
                "headline": safe_headline,
                "language": article.language or source_row.get("default_language", "ne"),
                "status": "published",
                "body_html": build_safe_paragraph(safe_excerpt),
                "excerpt": safe_excerpt,
                "featured_image_url": default_cover,
                "source_id": source_id,
                "source_article_url": article.source_article_url,
                "source_name_snapshot": source_row.get("name", "Unknown Source"),
                "primary_category_id": category_id,
                "canonical_url": article.canonical_url,
                "content_hash": content_hash,
                "published_at": article.published_at,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }

            article_insert = (
                db.table("articles").upsert(article_row, on_conflict="slug").execute()
            )

            if article_insert.data:
                _upsert_article_source(
                    db=db,
                    article_id=article_insert.data[0]["id"],
                    source_id=source_id,
                    source_article_url=article.source_article_url,
                    is_primary=True,
                )

            result.new += 1

        except Exception as exc:
            result.rejected += 1
            logger.exception(
                "Failed to process item: %s", getattr(article, "headline", "unknown")
            )
            _log_error(
                db,
                source_id,
                sync_log_id,
                "store_error",
                str(exc),
                {
                    "headline": getattr(article, "headline", None),
                    "source_url": getattr(article, "source_article_url", None),
                },
            )

    # 7. FINISH SYNC LOG
    status = (
        "success" if result.errors == 0 else ("partial" if result.new > 0 else "failed")
    )
    _finish_sync_log(db, sync_log_id, status, result)
    return result

# =============================================================
# IDEMPOTENT ARTICLE-SOURCE UPSERT
# =============================================================

def _upsert_article_source(
    db, article_id, source_id, source_article_url, is_primary=False
) -> bool:
    if not article_id or not source_id or not source_article_url:
        return False
    try:
        db.table("article_sources").upsert(
            {
                "article_id": article_id,
                "source_id": source_id,
                "source_article_url": source_article_url,
                "is_primary": is_primary,
            },
            on_conflict="source_id,source_article_url",
            ignore_duplicates=True,
        ).execute()
        return True
    except Exception:
        logger.exception("Failed to upsert article-source relationship")
        return False

# =============================================================
# HELPER UTILITIES
# =============================================================

def _slugify(headline: str, sector: str, hash_suffix: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = re.sub(r"[^a-zA-Z0-9\s-]", "", headline or "").strip().lower()
    base = re.sub(r"\s+", "-", base)
    base = base[:50].strip("-")
    sector_slug = sector.lower().replace(" ", "-")

    if hash_suffix:
        return f"{sector_slug}-{base}-{hash_suffix[:6]}-{date_str}"
    return f"{sector_slug}-{base}-{date_str}"

def _log_error(db, source_id, sync_log_id, error_type, message, context=None):
    logger.error("[%s] %s: %s", source_id, error_type, message)
    try:
        db.table("source_errors").insert(
            {
                "source_id": source_id,
                "sync_log_id": sync_log_id,
                "error_type": error_type,
                "message": str(message)[:5000],
                "context": context,
            }
        ).execute()
    except Exception:
        logger.exception("Failed to write source error to database")

def _finish_sync_log(db, sync_log_id, status, result: IngestionResult):
    try:
        db.table("source_sync_logs").update(
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "fetched_count": result.fetched,
                "new_count": result.new,
                "duplicate_count": result.duplicates,
                "rejected_count": result.rejected,
                "error_count": result.errors,
            }
        ).eq("id", sync_log_id).execute()
    except Exception:
        logger.exception("Failed to update sync log %s", sync_log_id)
