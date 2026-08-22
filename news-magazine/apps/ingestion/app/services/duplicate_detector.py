"""
Duplicate detection: canonical URL exact match, content hash exact match,
and fuzzy title similarity (catches the same story re-published with a
slightly different URL or headline by another outlet).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from app.sources.base import NormalizedArticle

TITLE_SIMILARITY_THRESHOLD = 0.90


class DuplicateDetector:
    def __init__(self, db):
        self.db = db

    def find_existing(self, article: NormalizedArticle) -> dict | None:
        content_hash = article.raw_metadata.get("content_hash")

        # 1. Exact content hash match
        if content_hash:
            res = (
                self.db.table("articles")
                .select("id, headline, canonical_url")
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]

        # 2. Exact canonical URL match
        res = (
            self.db.table("articles")
            .select("id, headline, canonical_url")
            .eq("canonical_url", article.canonical_url)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]

        # 3. Fuzzy title match among recent articles (last 3 days), to
        # catch the same story via a different source/URL. This is a
        # deliberately narrow window — cheap enough to run per-article
        # without needing a vector index at this scale.
        title_normalized = article.raw_metadata.get("title_normalized", article.headline.lower())
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        res = (
            self.db.table("articles")
            .select("id, headline, title_normalized, canonical_url")
            .gte("ingested_at", cutoff)
            .execute()
        )
        for candidate in res.data or []:
            candidate_title = candidate.get("title_normalized") or ""
            similarity = SequenceMatcher(None, title_normalized, candidate_title).ratio()
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                return candidate

        return None
