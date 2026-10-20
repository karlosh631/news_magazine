"""
Adapter registry.

Stage 1 hard-coded a one-entry dict (`ADAPTER_REGISTRY`) inside
services/ingestion.py, so adding an outlet meant editing the ingestion
pipeline. This module owns the mapping instead, keyed on
`sources.adapter_key`, which keeps adapter selection a data decision.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.sources.base import SourceAdapter
from app.sources.body import permits_full_text
from app.sources.generic_rss import GenericRssAdapter
from app.sources.html_listing import HtmlListingAdapter

logger = logging.getLogger("adapters")

ADAPTER_REGISTRY: dict[str, type[SourceAdapter]] = {
    "generic_rss": GenericRssAdapter,
    "html_listing": HtmlListingAdapter,
}


def body_status(source_row: dict) -> str:
    """
    Why this outlet's articles do or do not carry a full body.

    `full_body_html` is produced by `attach_full_body()` in
    app/sources/body.py behind two independent gates, and when it does
    not fire nothing says so: the article is stored, the "read more"
    disclosure appears, and the reader expands a summary into itself.
    That is exactly how a dead feature stays dead — the row looks
    healthy either way.

    So the admin surface reports the gate rather than the absence.
    Ordered most-blocking first, because that is the order in which
    the operator has to fix them:

      no_licence     sources.republish_permission is false. Nothing
                     else matters: this is a licensing decision, and
                     the code refuses to guess it. THE REAL GATE.
      free_path_off  ENRICH_BODY is false. Costs no requests, so
                     turning it on is free.
      fetch_only     ENRICH_BODY is on but FETCH_FULL_TEXT is off, so
                     only stories whose page media enrichment already
                     fetched get a body — the rest need a request.
      ready          A licensed body will be stored when a page fetch
                     recovers a body-length article.

    Read-only. Never touches the network or the database.
    """
    if not permits_full_text(source_row):
        return "no_licence"

    settings = get_settings()
    if not getattr(settings, "enrich_body", False):
        return "free_path_off"
    if not getattr(settings, "fetch_full_text", False):
        return "fetch_only"
    return "ready"


def build_adapter(source_row: dict) -> SourceAdapter:
    """
    Instantiate the adapter named by a sources row.

    Raises KeyError for an unknown adapter_key so the caller can record
    a precise "unknown_adapter" error against that source rather than
    silently skipping it.
    """
    key = (source_row.get("adapter_key") or "").strip()
    adapter_cls = ADAPTER_REGISTRY.get(key)

    if adapter_cls is None:
        raise KeyError(
            f"No adapter registered for key {key!r}. "
            f"Known keys: {', '.join(sorted(ADAPTER_REGISTRY))}."
        )

    return adapter_cls(source_row)


def available_adapters() -> list[str]:
    return sorted(ADAPTER_REGISTRY)


def describe(source_row: dict) -> dict[str, Any]:
    """Adapter metadata for the admin/health surface."""
    key = source_row.get("adapter_key")
    cls = ADAPTER_REGISTRY.get(key)
    return {
        "adapter_key": key,
        "registered": cls is not None,
        "class": cls.__name__ if cls else None,
        "feed_url": source_row.get("feed_url"),
        "fallback_url": source_row.get("fallback_url"),
        "poll_interval_seconds": source_row.get("poll_interval_seconds"),
        "rate_limit_per_minute": source_row.get("rate_limit_per_minute"),
        # Whether a full body will be stored for this outlet, and if
        # not, which gate is holding it back. See body_status().
        "republish_permission": bool(
            source_row.get("republish_permission", False)
        ),
        "body_status": body_status(source_row),
    }