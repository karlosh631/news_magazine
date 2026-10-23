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
    """
    Caches robots.txt parses per host for the life of the process.

    TWO THINGS ARE TRUE HERE THAT WERE NOT TRUE BEFORE, and both of them
    matter to a publisher who has started refusing us.

    1. The cache is shared by every adapter in the process.
       It used to be created per adapter, and an adapter is created per
       poll, so robots.txt was downloaded again on EVERY poll of EVERY
       outlet - one extra request to each publisher, per poll, forever,
       for a file that says the same thing every time. A polite client
       asks once.

    2. The publisher's own CRAWL-DELAY is read.
       `Crawl-delay` and `Request-rate` are the one part of robots.txt
       that says *how often* the publisher wants to be asked, and this
       code never looked at them. ratopati.com publishes

           User-agent: *
           Disallow:
           Crawl-delay: 20

       which means "at most one request every 20 seconds", and the
       worker was asking it every 7.5 seconds (eight requests a minute)
       without ever having read that line. `crawl_delay()` returns the
       delay in seconds for the User-Agent we identify as, or 0.0 when
       the publisher states none.
    """

    def __init__(self) -> None:
        self._cache: dict[str, robotparser.RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _parser(
        self,
        url: str,
        user_agent: str,
    ) -> robotparser.RobotFileParser:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"

        if host not in self._cache:
            # One download per host, even when two rows for that host
            # are polled at the same moment: the worker starts every
            # outlet at once, and a shared cache with no lock would let
            # two of them ask for the same file.
            lock = self._locks.setdefault(host, asyncio.Lock())

            async with lock:
                if host not in self._cache:
                    rp = robotparser.RobotFileParser()
                    robots_url = f"{host}/robots.txt"
                    try:
                        async with httpx.AsyncClient(
                            timeout=10.0,
                            # A robots.txt that redirects is normal -
                            # ratopati.com serves its rules at
                            # www.ratopati.com/robots.txt and answers
                            # 301 for the bare host. Not following the
                            # redirect means the rules (and the
                            # Crawl-delay) are read as "none", which is
                            # the one answer a publisher never meant.
                            follow_redirects=True,
                        ) as client:
                            resp = await client.get(
                                robots_url,
                                headers={"User-Agent": user_agent},
                            )
                        if resp.status_code == 200:
                            rp.parse(resp.text.splitlines())
                        else:
                            # No robots.txt or inaccessible -> be
                            # conservative and treat it as "unknown";
                            # adapters should require an explicit admin
                            # allow-flag regardless of this result.
                            rp.parse([])
                    except httpx.HTTPError:
                        rp.parse([])
                    self._cache[host] = rp

        return self._cache[host]

    async def is_allowed(self, url: str, user_agent: str) -> bool:
        parser = await self._parser(url, user_agent)
        return parser.can_fetch(user_agent, url)

    async def crawl_delay(self, url: str, user_agent: str) -> float:
        """
        The delay the publisher asks for between requests, in seconds.

        0.0 when the publisher states none, which is the case for
        every Nepali outlet except ratopati.com.
        """
        parser = await self._parser(url, user_agent)

        try:
            delay = parser.crawl_delay(user_agent)
        except Exception:
            return 0.0

        if delay:
            return float(delay)

        try:
            rate = parser.request_rate(user_agent)
        except Exception:
            return 0.0

        if rate and rate.requests and rate.seconds:
            return float(rate.seconds) / float(rate.requests)

        return 0.0


class RateLimiter:
    """
    The request budget for ONE publisher, shared by every adapter that
    talks to that publisher.

    WHAT CHANGED, AND WHY IT MATTERS
    -------------------------------
    This was created per adapter, and an adapter is created per poll,
    so the budget was thrown away at the end of every poll: the
    "eight requests a minute" allowance was spent by the feed request,
    forgotten, and the next poll started again from scratch. Two polls
    ninety seconds apart never added up to anything, and nothing the
    code did to a publisher was ever remembered.

    It is now one object per HOST, kept for the life of the process, so
    the budget covers every poll of that publisher. When the publisher
    states a Crawl-delay in its robots.txt, that delay is the FLOOR
    however generous the row's own rate limit is - the publisher's own
    words beat our own default.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._crawl_delay = 0.0
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    # -- configuration -------------------------------------------------

    def tighten(self, requests_per_minute: int) -> None:
        """
        Apply a rate limit, keeping the stricter of the two.

        Two rows can point at one host (an edition and its parent, a
        feed and a listing). They share one budget, and the stricter
        rate is the one that counts.
        """
        interval = 60.0 / max(requests_per_minute, 1)
        if interval > self._min_interval:
            self._min_interval = interval

    def set_crawl_delay(self, seconds: float) -> None:
        """
        The delay the publisher stated in robots.txt becomes the floor.
        """
        if seconds and seconds > self._crawl_delay:
            self._crawl_delay = float(seconds)

    @property
    def interval(self) -> float:
        """Seconds between two requests to this publisher, as enforced."""
        return max(self._min_interval, self._crawl_delay)

    @property
    def crawl_delay(self) -> float:
        return self._crawl_delay

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            pause = self.interval - elapsed
            if pause > 0:
                await asyncio.sleep(pause)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# SHARED, PER-HOST STATE
#
# robots.txt is fetched once per host, and one request budget is kept
# per host, for the life of the process. Both used to be per-adapter,
# which is per-poll, which is to say: never.
# ---------------------------------------------------------------------------

_shared_robots = RobotsChecker()
_shared_budgets: dict[str, RateLimiter] = {}


def shared_robots_checker() -> RobotsChecker:
    """The one robots.txt cache this process uses."""
    return _shared_robots


def host_budget(host: str, requests_per_minute: int) -> RateLimiter:
    """
    The one request budget for one host, shared by every adapter.

    An empty host (a row with no URL at all) gets a budget of its own,
    so two broken rows cannot throttle each other.
    """
    key = (host or "").strip().lower()

    budget = _shared_budgets.get(key)

    if budget is None:
        budget = RateLimiter(requests_per_minute)
        _shared_budgets[key] = budget
    else:
        budget.tighten(requests_per_minute)

    return budget


def reset_shared_politeness_state() -> None:
    """
    Forget every shared robots.txt parse and every shared budget.

    Only the tests need this; a running worker keeps both for the life
    of the process on purpose.
    """
    _shared_robots._cache.clear()
    _shared_robots._locks.clear()
    _shared_budgets.clear()


def source_host(source_row: dict) -> str:
    """
    The host a source row talks to, from whichever URL it uses.
    """
    for field in ("feed_url", "fallback_url", "base_url", "site_url"):
        value = source_row.get(field)
        if value:
            parsed = urlparse(str(value))
            if parsed.netloc:
                return parsed.netloc
    return ""


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

        # One robots.txt cache and one request budget per HOST, for the
        # life of the process - not one of each per poll, which is what
        # this used to be. See RobotsChecker and RateLimiter for why.
        self.robots = shared_robots_checker()
        self.rate_limiter = host_budget(
            source_host(source_row),
            source_row.get("rate_limit_per_minute", 6),
        )

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

        user_agent = self.settings.default_user_agent

        allowed = await self.robots.is_allowed(url, user_agent)

        # The publisher states how often it wants to be asked. That
        # statement is now read and becomes the floor for this host's
        # request budget, so a row's own "eight requests a minute"
        # cannot outvote a robots.txt that says "one every 20 seconds".
        if allowed and getattr(self.settings, "respect_robots_crawl_delay", True):
            delay = await self.robots.crawl_delay(url, user_agent)
            self.rate_limiter.set_crawl_delay(delay)

        return allowed
