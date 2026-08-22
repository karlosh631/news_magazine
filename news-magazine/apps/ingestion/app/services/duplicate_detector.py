from __future__ import annotations

import hashlib
import re
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)


class DuplicateDetector:
    """
    Detect duplicate articles using stable identity signals.

    Detection signals, in priority order:

    1. Canonical URL
    2. Content hash
    3. Normalized title

    IMPORTANT:
    Application-level duplicate detection is only a pre-check.

    The database MUST also have appropriate UNIQUE constraints/indexes
    because two ingestion workers can perform the following concurrently:

        Worker A -> check -> no duplicate
        Worker B -> check -> no duplicate
        Worker A -> insert
        Worker B -> insert

    PostgreSQL uniqueness is therefore the final authority.
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

    DEFAULT_SCHEMES = {
        "http",
        "https",
    }

    def __init__(self, db):
        self.db = db

    # =========================================================
    # URL NORMALIZATION
    # =========================================================

    @classmethod
    def normalize_url(
        cls,
        url: str | None,
    ) -> str:
        """
        Normalize an article URL into a stable canonical form.

        Changes:
        - Lowercases scheme and hostname.
        - Removes default HTTP/HTTPS ports.
        - Removes tracking parameters.
        - Sorts query parameters.
        - Removes fragments.
        - Removes trailing slash except root.
        - Preserves legitimate query parameters.

        Invalid URLs return an empty string.
        """

        if not url:
            return ""

        value = str(url).strip()

        if not value:
            return ""

        try:

            parts = urlsplit(value)

            scheme = (
                parts.scheme.lower()
            )

            hostname = (
                parts.hostname or ""
            ).lower().rstrip(".")

            # -------------------------------------------------
            # Require HTTP/HTTPS
            # -------------------------------------------------

            if scheme not in cls.DEFAULT_SCHEMES:
                return ""

            if not hostname:
                return ""

            # -------------------------------------------------
            # Normalize port
            # -------------------------------------------------

            try:
                port = parts.port
            except ValueError:
                return ""

            netloc = hostname

            if port is not None:

                is_default_port = (
                    (
                        scheme == "http"
                        and port == 80
                    )
                    or
                    (
                        scheme == "https"
                        and port == 443
                    )
                )

                if not is_default_port:
                    netloc = (
                        f"{hostname}:{port}"
                    )

            # -------------------------------------------------
            # Normalize query parameters
            # -------------------------------------------------

            query_items = []

            for key, value in parse_qsl(
                parts.query,
                keep_blank_values=True,
            ):

                if (
                    key.lower()
                    in cls.TRACKING_PARAMETERS
                ):
                    continue

                query_items.append(
                    (
                        key,
                        value,
                    )
                )

            # Sort query parameters so:

            # ?a=1&b=2

            # and:

            # ?b=2&a=1

            # become identical.

            query = urlencode(
                sorted(
                    query_items,
                    key=lambda item: (
                        item[0],
                        item[1],
                    ),
                ),
                doseq=True,
            )

            # -------------------------------------------------
            # Normalize path
            # -------------------------------------------------

            path = parts.path or "/"

            if path != "/":
                path = path.rstrip("/")

            if not path:
                path = "/"

            # -------------------------------------------------
            # Fragments are intentionally removed.
            # -------------------------------------------------

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
            return ""

    # =========================================================
    # TITLE NORMALIZATION
    # =========================================================

    @staticmethod
    def normalize_title(
        title: str | None,
    ) -> str:
        """
        Normalize an article title for duplicate detection.

        This does NOT remove meaningful words.

        Example:

            "  Nepal's Economy  Grows! "

        becomes approximately:

            "nepal's economy grows"
        """

        if not title:
            return ""

        value = str(title)

        # Normalize Unicode whitespace.
        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        value = value.strip().lower()

        # Remove surrounding punctuation only.
        value = re.sub(
            r"^[^\w]+|[^\w]+$",
            "",
            value,
            flags=re.UNICODE,
        )

        return value.strip()

    # =========================================================
    # TITLE HASH
    # =========================================================

    @classmethod
    def title_hash(
        cls,
        title: str | None,
    ) -> str:

        normalized = cls.normalize_title(
            title
        )

        if not normalized:
            return ""

        return hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()

    # =========================================================
    # CONTENT HASH
    # =========================================================

    @staticmethod
    def normalize_content_hash(
        content_hash: str | None,
    ) -> str:

        if not content_hash:
            return ""

        return str(
            content_hash
        ).strip().lower()

    # =========================================================
    # ARTICLE IDENTITY
    # =========================================================

    def get_identity(
        self,
        article,
    ) -> dict:
        """
        Generate all stable identity signals for an article.

        This keeps normalization logic in one place so the ingestion
        pipeline and duplicate detector do not disagree about identity.
        """

        canonical_url = self.normalize_url(
            getattr(
                article,
                "canonical_url",
                None,
            )
            or getattr(
                article,
                "source_article_url",
                None,
            )
        )

        normalized_title = (
            self.normalize_title(
                getattr(
                    article,
                    "headline",
                    None,
                )
            )
        )

        content_hash = ""

        raw_metadata = getattr(
            article,
            "raw_metadata",
            None,
        )

        if isinstance(
            raw_metadata,
            dict,
        ):

            content_hash = (
                self.normalize_content_hash(
                    raw_metadata.get(
                        "content_hash"
                    )
                )
            )

        return {
            "canonical_url": canonical_url,
            "content_hash": content_hash,
            "title_normalized": normalized_title,
            "title_hash": self.title_hash(
                normalized_title
            ),
        }

    # =========================================================
    # FIND EXISTING ARTICLE
    # =========================================================

    def find_existing(
        self,
        article,
    ) -> dict | None:
        """
        Search for an existing article using strong identity signals.

        Priority:

            1. canonical_url
            2. content_hash
            3. title_normalized

        This function is intentionally only a pre-insert duplicate check.

        The database UNIQUE constraints remain the final protection
        against concurrent duplicate inserts.
        """

        identity = self.get_identity(
            article
        )

        canonical_url = identity[
            "canonical_url"
        ]

        content_hash = identity[
            "content_hash"
        ]

        normalized_title = identity[
            "title_normalized"
        ]

        # =====================================================
        # 1. CANONICAL URL
        # =====================================================

        if canonical_url:

            try:

                response = (
                    self.db.table(
                        "articles"
                    )
                    .select("*")
                    .eq(
                        "canonical_url",
                        canonical_url,
                    )
                    .limit(1)
                    .execute()
                )

                if response.data:
                    return response.data[0]

            except Exception:
                # Do not silently convert a database outage into
                # "no duplicate". Re-raise so ingestion records
                # the failure correctly.
                raise

        # =====================================================
        # 2. CONTENT HASH
        # =====================================================

        if content_hash:

            try:

                response = (
                    self.db.table(
                        "articles"
                    )
                    .select("*")
                    .eq(
                        "content_hash",
                        content_hash,
                    )
                    .limit(1)
                    .execute()
                )

                if response.data:
                    return response.data[0]

            except Exception:
                raise

        # =====================================================
        # 3. NORMALIZED TITLE
        # =====================================================

        if normalized_title:

            try:

                response = (
                    self.db.table(
                        "articles"
                    )
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

            except Exception:
                raise

        return None

    # =========================================================
    # FIND BY CANONICAL URL
    # =========================================================

    def find_by_canonical_url(
        self,
        canonical_url: str | None,
    ) -> dict | None:

        normalized_url = (
            self.normalize_url(
                canonical_url
            )
        )

        if not normalized_url:
            return None

        response = (
            self.db.table(
                "articles"
            )
            .select("*")
            .eq(
                "canonical_url",
                normalized_url,
            )
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]

        return None

    # =========================================================
    # FIND BY CONTENT HASH
    # =========================================================

    def find_by_content_hash(
        self,
        content_hash: str | None,
    ) -> dict | None:

        normalized_hash = (
            self.normalize_content_hash(
                content_hash
            )
        )

        if not normalized_hash:
            return None

        response = (
            self.db.table(
                "articles"
            )
            .select("*")
            .eq(
                "content_hash",
                normalized_hash,
            )
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]

        return None

    # =========================================================
    # FIND BY NORMALIZED TITLE
    # =========================================================

    def find_by_title(
        self,
        title: str | None,
    ) -> dict | None:

        normalized_title = (
            self.normalize_title(
                title
            )
        )

        if not normalized_title:
            return None

        response = (
            self.db.table(
                "articles"
            )
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
