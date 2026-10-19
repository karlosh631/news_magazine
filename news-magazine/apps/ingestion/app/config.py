"""
Environment-driven configuration for the ingestion service.

Never hard-code secrets here — everything comes from the environment,
loaded from .env locally and from the host's environment settings in
production.

Stage 1 declared `max_concurrent_fetches` and `max_article_rows` but the
values were never actually consulted by the scheduler or the pruner, so
the knobs did nothing. Everything here now has a caller.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------
    # Supabase (required)
    # ---------------------------------------------------------
    supabase_url: str
    # Server-side only; bypasses RLS by design.
    supabase_service_role_key: str

    # ---------------------------------------------------------
    # Shared secrets
    # ---------------------------------------------------------
    ingestion_secret: str
    cron_secret: str

    # ---------------------------------------------------------
    # Networking / politeness
    # ---------------------------------------------------------
    # Identify yourself honestly to every outlet. Several Nepali
    # publishers return a challenge page to generic bot strings.
    default_user_agent: str = (
        "NepalNewsMagazineBot/1.0 "
        "(+https://new24hr.vercel.app; contact=admin@new24hr.vercel.app)"
    )
    default_request_timeout_seconds: float = 15.0

    # ---------------------------------------------------------
    # Scheduler
    # ---------------------------------------------------------
    scheduler_enabled: bool = True
    # Floor applied to any sources.poll_interval_seconds. Never poll a
    # single outlet faster than this.
    min_poll_seconds: int = 60

    # ---------------------------------------------------------
    # Retention
    # ---------------------------------------------------------
    # Articles are deleted this long after publication. A pruned story
    # comes back when someone searches for it (see /api/scrape-on-demand).
    retention_hours: int = 48
    # Hard ceiling applied after the TTL pass.
    max_article_rows: int = 10_000

    # ---------------------------------------------------------
    # Deduplication
    # ---------------------------------------------------------
    dedup_window_hours: int = 12
    # rapidfuzz score (0-100) at or above which two headlines are the
    # same story. 78 separates real Nepali wire copy (measured 78.7-83.0)
    # from unrelated stories (measured 43.3).
    fuzzy_title_threshold: int = 78

    # ---------------------------------------------------------
    # On-demand search
    # ---------------------------------------------------------
    max_requests_per_hour: int = 5
    scrape_budget_seconds: float = 18.0

    # ---------------------------------------------------------
    # Media enrichment
    # ---------------------------------------------------------
    # Fetching each article page for og:image / video / audio is what
    # makes real media appear. Turning this off leaves feed-only
    # thumbnails, which is why images rendered blank before.
    enrich_media: bool = True

    # ---------------------------------------------------------
    # Image validation
    #
    # Every image URL used to be chosen on the server by guessing
    # from the URL's shape, and then stored unverified. Some of
    # those guesses point at URLs that 403 or 404 forever — an
    # assets-cdn.kathmandupost.com request with a foreign referer
    # is refused outright — so the feed carried permanently dead
    # links and the only party that found out was the reader's
    # browser. This asks each URL to prove it returns an image
    # before the row is written, so the guarantee is made once,
    # server-side, for every reader on every page.
    #
    # Costs one ranged GET per NEW image, deduplicated per poll by
    # URL, so outlets that reuse one image across stories pay for
    # it once rather than per article.
    # ---------------------------------------------------------
    validate_images: bool = True
    image_validation_timeout_seconds: float = 6.0
    # Safety valve: how many renditions of one article may be probed
    # before giving up on it. Almost every article has one candidate
    # or two; this only bounds the pathological case.
    max_image_candidates_per_article: int = 3

    # ---------------------------------------------------------
    # Gemini (optional)
    #
    # NOT needed for the four-point summaries: those are extracted
    # verbatim from each outlet's own article text.
    # ---------------------------------------------------------
    gemini_api_key: str | None = None
    # May rewrite the stored excerpt. Its failure can never reject an
    # article or block a sync.
    enable_gemini_summary: bool = False
    # May reorder the four already-extracted points. It cannot introduce
    # new text: anything not already in the points is discarded.
    enable_gemini_ranking: bool = False

    # ---------------------------------------------------------
    # Reels (optional)
    #
    # Short-form video for the /category/reels page. Only METADATA is
    # stored; playback streams from the provider CDN.
    #
    # Every provider here licenses its content for redistribution —
    # that is the only reason any of this is here. Wikimedia Commons
    # needs no key at all, so the category works unconfigured; Pexels
    # and Pixabay add coverage when keys are present.
    #
    # Multiple keys are comma-separated. The pool rotates across them
    # and skips any that are rate limited, so one exhausted key does
    # not take the category down.
    # ---------------------------------------------------------
    enable_reels: bool = False
    reels_per_source: int = 30
    reels_poll_seconds: int = 900
    pexels_api_keys: str | None = None      # comma-separated
    pixabay_api_keys: str | None = None     # comma-separated
    # Comma-separated hints, rotated one per pass.
    #
    # The default is deliberately NOT empty. With no hint, the keyless
    # Wikimedia adapter asks Commons for its newest video uploads and
    # sorts by upload time, which fills a Nepal news site's Reels tab
    # with unrelated scientific timelapses — a first pass here stored 30
    # NASA SDO clips. Terms keep the category on subject by default; add
    # your own comma-separated to widen it.
    reels_search_terms: str | None = (
        "nepal,kathmandu,himalaya,everest,terai,pokhara,bhaktapur"
    )
    reels_http_timeout: float = 20.0
    # Wikimedia's API policy asks every client to identify itself and
    # says anonymous traffic is what gets access withdrawn. This is a
    # contact address that reaches a human, not a version string.
    # It lives here rather than in os.environ so that setting it in
    # .env works, exactly as every other setting in this file does.
    reels_user_agent: str = (
        "NepalNewsMagazineReels/1.0 (+https://new24hr.vercel.app)"
    )

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "console"  # console | json


@lru_cache
def get_settings() -> Settings:
    return Settings()