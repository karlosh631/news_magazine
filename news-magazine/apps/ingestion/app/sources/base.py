"""
Base interface every source adapter must implement, plus the shared
politeness machinery (robots.txt checks, rate limiting, backoff).

Design rule (non-negotiable): an adapter is only allowed to run if
`source.allowed = True` in the database, which an administrator sets only
after manually verifying:
  1. robots.txt permits fetching the feed/API path
  2. the source's Terms of Service permit this kind of automated access
  3. a legitimate RSS/Atom feed or public API is being used — not HTML
     scraping that defeats access controls, paywalls, or anti-bot measures

Adapters never bypass CAPTCHAs, authentication, or anti-bot protection.
If a source doesn't offer a permitted feed/API, it stays disabled instead
of growing a scraper for it.
"""
from __future__ import annotations

import asyncio
import time
import urllib.robotparser as robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.config import get_settings


@dataclass
class NormalizedArticle:
    """The common shape every adapter must produce, regardless of source."""
    source_article_url: str
    canonical_url: str
    headline: str
    published_at: str | None  # ISO 8601
    category_hint: str | None = None
    author_name: str | None = None
    excerpt: str | None = None          # permitted summary only
    thumbnail_url: str | None = None
    full_body_html: str | None = None   # ONLY if source.republish_permission is True
    language: str = "ne"
    raw_metadata: dict = field(default_factory=dict)


class RobotsChecker:
    """Caches robots.txt parses per host for the life of the process."""

    def __init__(self) -> None:
        self._cache: dict[str, robotparser.RobotFileParser] = {}

    async def is_allowed(self, url: str, user_agent: str) -> bool:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._cache:
            rp = robotparser.RobotFileParser()
            robots_url = f"{host}/robots.txt"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(robots_url, headers={"User-Agent": user_agent})
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt or inaccessible -> be conservative and
                    # treat it as "unknown"; adapters should require an
                    # explicit admin allow-flag regardless of this result.
                    rp.parse([])
            except httpx.HTTPError:
                rp.parse([])
            self._cache[host] = rp
        return self._cache[host].can_fetch(user_agent, url)


class RateLimiter:
    """Simple per-host token bucket so we never hammer a source."""

    def __init__(self, requests_per_minute: int) -> None:
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


class SourceAdapter(ABC):
    """
    Every concrete adapter (generic_rss, and any future source-specific
    adapter) implements this interface. The ingestion pipeline
    (services/ingestion.py) only ever talks to this interface — it never
    special-cases a source by name.
    """

    def __init__(self, source_row: dict):
        self.source = source_row
        self.settings = get_settings()
        self.robots = RobotsChecker()
        self.rate_limiter = RateLimiter(source_row.get("rate_limit_per_minute", 6))

    @abstractmethod
    async def fetch(self) -> str:
        """Retrieve raw feed/API content. Must respect rate limiting."""

    @abstractmethod
    def parse(self, raw: str) -> list[dict]:
        """Parse raw content into a list of loosely-structured entries."""

    @abstractmethod
    def normalize(self, entries: list[dict]) -> list[NormalizedArticle]:
        """Convert parsed entries into NormalizedArticle objects."""

    def validate(self, articles: list[NormalizedArticle]) -> list[NormalizedArticle]:
        """Drop entries missing required fields. Adapters may override/extend."""
        valid = []
        for a in articles:
            if not a.headline or not a.source_article_url or not a.canonical_url:
                continue
            # Full-text republication is only ever kept if the source row
            # explicitly grants it — this is enforced here, not trusted
            # from the parser, in case an adapter bug leaks full content.
            if not self.source.get("republish_permission", False):
                a.full_body_html = None
            valid.append(a)
        return valid

    async def check_allowed(self, url: str) -> bool:
        if not self.source.get("allowed", False):
            return False
        return await self.robots.is_allowed(url, self.settings.default_user_agent)
