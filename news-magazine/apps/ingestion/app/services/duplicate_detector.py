from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class DuplicateDetector:
    """
    Detects duplicate articles using multiple stable signals:

    1. Canonical URL
    2. Content hash
    3. Normalized title

    The detector is intentionally conservative: if any strong existing
    identity signal matches, the article is treated as a duplicate.
    """

    TRACKING_PARAMETERS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
    }

    def __init__(self, db):
        self.db = db

    @classmethod
    def normalize_url(cls, url: str | None) -> str:
        if not url:
            return ""

        try:
            parts = urlsplit(url.strip())

            scheme = parts.scheme.lower()
            hostname = (parts.hostname or "").lower()

            if not scheme or not hostname:
                return url.strip()

            # Remove default ports.
            netloc = hostname

            if parts.port:
                if not (
                    (scheme == "http" and parts.port == 80)
                    or (scheme == "https" and parts.port == 443)
                ):
                    netloc = f"{hostname}:{parts.port}"

            # Remove common tracking query parameters.
            query_items = [
                (key, value)
                for key, value in parse_qsl(
                    parts.query,
                    keep_blank_values=True,
                )
                if key.lower() not in cls.TRACKING_PARAMETERS
            ]

            query = urlencode(
                sorted(query_items),
                doseq=True,
            )

            path = parts.path or "/"

            # Remove trailing slash except root.
            if path != "/":
                path = path.rstrip("/")

            return urlunsplit(
                (
                    scheme,
                    netloc,
                    path,
                    query,
                    "",
                )
            )

        except Exception:
            return url.strip()

    @staticmethod
    def normalize_title(
        title: str | None,
    ) -> str:
        if not title:
            return ""

        value = title.lower().strip()

        # Normalize Unicode-ish whitespace.
        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        # Remove surrounding punctuation.
        value = re.sub(
            r"^[^\w]+|[^\w]+$",
            "",
            value,
            flags=re.UNICODE,
        )

        return value.strip()

    @classmethod
    def title_hash(
        cls,
        title: str | None,
    ) -> str:
        normalized = cls.normalize_title(title)

        return hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()

    def find_existing(
        self,
        article,
    ) -> dict | None:
        """
        Search for an existing article using progressively weaker
        duplicate signals.
        """

        canonical_url = self.normalize_url(
            article.canonical_url
            or article.source_article_url
        )

        content_hash = None

        if article.raw_metadata:
            content_hash = article.raw_metadata.get(
                "content_hash"
            )

        normalized_title = self.normalize_title(
            article.headline
        )

        # -----------------------------------------------------
        # 1. Canonical URL
        # -----------------------------------------------------

        if canonical_url:
            response = (
                self.db.table("articles")
                .select("*")
                .eq("canonical_url", canonical_url)
                .limit(1)
                .execute()
            )

            if response.data:
                return response.data[0]

        # -----------------------------------------------------
        # 2. Content hash
        # -----------------------------------------------------

        if content_hash:
            response = (
                self.db.table("articles")
                .select("*")
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )

            if response.data:
                return response.data[0]

        # -----------------------------------------------------
        # 3. Normalized title
        # -----------------------------------------------------

        if normalized_title:
            response = (
                self.db.table("articles")
                .select("*")
                .eq(
                    "title_normalized",
                    normalized_title,
                )
                .limit(1)
                .execute()
            )

            if response.data:
                return response.data[0]

        return None
