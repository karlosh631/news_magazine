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

from app.sources.base import SourceAdapter
from app.sources.generic_rss import GenericRssAdapter
from app.sources.html_listing import HtmlListingAdapter

logger = logging.getLogger("adapters")

ADAPTER_REGISTRY: dict[str, type[SourceAdapter]] = {
    "generic_rss": GenericRssAdapter,
    "html_listing": HtmlListingAdapter,
}


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
    }