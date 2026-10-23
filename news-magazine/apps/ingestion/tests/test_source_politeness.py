"""
Being a polite client, and being a client at all.

THE PROBLEM
-----------
Five Nepali publishers (Ekantipur, Nepal News, Nepal News English,
Ratopati, Ujyaalo Online) started answering this worker with HTTP 403.
The paths were fine - every one of those URLs answers 200 from an
ordinary client, with the worker's own User-Agent, with a browser
User-Agent, with a Referer and with Accept-Language - and so were the
robots.txt files: every one of them allows the paths we use.

What was wrong was on our side of the wire, and there were three
separate things wrong:

  1. robots.txt was downloaded AGAIN on every poll of every outlet,
     because the cache belonged to the adapter and an adapter is built
     per poll. One extra request to each publisher, per poll, for a
     file that says the same thing every time.

  2. The publisher's own "Crawl-delay" was never read. ratopati.com
     publishes "Crawl-delay: 20" - one request every twenty seconds -
     and the worker was asking it every 7.5 seconds, never having
     looked at the line.

  3. The request budget was per adapter, so it was thrown away at the
     end of every poll. Eight requests a minute was, in practice, one
     request per poll with the allowance spent and forgotten.

WHAT IS ASSERTED HERE
---------------------
That the politeness machinery is real rather than decorative:

  * robots.txt is fetched once per host, not once per poll
  * the Crawl-delay a publisher states is read
  * that Crawl-delay becomes the floor for the host's budget, however
    generous the row's own rate limit is
  * two rows that point at one host share one budget, and the
    stricter of the two rates is the one that counts
  * a feed request carries a language and the outlet's own referer,
    and still identifies itself honestly - a browser User-Agent is NOT
    used to slip past a refusal, because the refusal was never about
    the User-Agent
  * migration 0030 asks these five publishers less often than before
    and does not disable any of them
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.sources import base
from app.sources.base import (
    RateLimiter,
    RobotsChecker,
    host_budget,
    reset_shared_politeness_state,
    source_host,
)
from app.sources.generic_rss import GenericRssAdapter


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    """The little of httpx.Response the code under test actually uses."""

    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("GET", "https://example.invalid/"),
                response=None,
            )


ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"
ROBOTS_WITH_DELAY = "User-agent: *\nDisallow:\nCrawl-delay: 20\n"
FEED = (
    "<?xml version='1.0'?><rss version='2.0'><channel>"
    "<item><title>Headline about the committee</title>"
    "<link>https://example.invalid/a</link>"
    "<pubDate>Wed, 21 Oct 2026 10:00:00 +0000</pubDate></item>"
    "</channel></rss>"
)


def fake_get_factory(robots_text: str = ROBOTS_ALLOW_ALL, feed_text: str = FEED):
    """
    A replacement for httpx.AsyncClient.get that answers from memory.

    Returns (fake_get, calls) where `calls` is the list of URLs asked
    for, so a test can count how many robots.txt downloads a host cost.
    """
    calls: list[str] = []

    async def fake_get(self, url, **kwargs):
        calls.append(str(url))
        if str(url).endswith("/robots.txt"):
            return FakeResponse(200, robots_text)
        return FakeResponse(200, feed_text)

    return fake_get, calls


@pytest.fixture(autouse=True)
def clean_shared_state():
    """
    Every test starts with no robots.txt cached and no budget shared.

    Both are process-wide on purpose (that is the fix), so they have to
    be cleared explicitly or one test's budget is another test's sleep.
    """
    reset_shared_politeness_state()
    yield
    reset_shared_politeness_state()


def settings_stub(**overrides) -> SimpleNamespace:
    base_settings = {
        "default_user_agent": "NepalNewsMagazineBot/1.0 (+https://example.test)",
        "default_request_timeout_seconds": 15.0,
        "default_accept_language": "ne,en;q=0.8",
        "respect_robots_crawl_delay": True,
        "rss_page_size": 100,
        "rss_page_size_param": "posts_per_page",
        "rss_feed_pages": 1,
        "rss_wordpress_hosts": None,
    }
    base_settings.update(overrides)
    return SimpleNamespace(**base_settings)


def adapter_for(**row_overrides) -> GenericRssAdapter:
    row = {
        "name": "Test Outlet",
        "feed_url": "https://example.invalid/feed",
        "base_url": "https://example.invalid",
        "site_url": "https://example.invalid",
        "adapter_key": "generic_rss",
        "enabled": True,
        "allowed": True,
        "rate_limit_per_minute": 600,
        "default_language": "ne",
        "republish_permission": False,
    }
    row.update(row_overrides)
    adapter = GenericRssAdapter(row)
    adapter.settings = settings_stub()
    return adapter


# ---------------------------------------------------------------------------
# robots.txt: once per host, and the delay it states
# ---------------------------------------------------------------------------

async def test_robots_txt_is_fetched_once_per_host_not_once_per_poll(monkeypatch):
    """
    The same host, two polls: one robots.txt download, not two.

    It used to be one per poll, because the cache belonged to the
    adapter and an adapter is built per poll. Every outlet paid a
    request for a file that never changes.
    """
    fake_get, calls = fake_get_factory()
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    first = adapter_for()
    await first.check_allowed("https://example.invalid/feed")

    second = adapter_for()
    await second.check_allowed("https://example.invalid/feed")

    robots_calls = [c for c in calls if c.endswith("/robots.txt")]

    assert robots_calls == ["https://example.invalid/robots.txt"]


async def test_robots_txt_is_fetched_once_per_host_across_different_hosts(monkeypatch):
    """Two hosts, two downloads - the cache is per host, not global."""
    fake_get, calls = fake_get_factory()
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await adapter_for(feed_url="https://one.invalid/feed").check_allowed(
        "https://one.invalid/feed"
    )
    await adapter_for(feed_url="https://two.invalid/feed").check_allowed(
        "https://two.invalid/feed"
    )

    robots_calls = sorted(c for c in calls if c.endswith("/robots.txt"))

    assert robots_calls == [
        "https://one.invalid/robots.txt",
        "https://two.invalid/robots.txt",
    ]


async def test_two_polls_of_one_host_at_the_same_moment_download_robots_once(monkeypatch):
    """
    The worker starts every outlet at once. Two rows for one host
    polling together must not ask for the same file twice.
    """
    fake_get, calls = fake_get_factory()
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    import asyncio

    await asyncio.gather(
        adapter_for().check_allowed("https://example.invalid/feed"),
        adapter_for().check_allowed("https://example.invalid/feed"),
    )

    robots_calls = [c for c in calls if c.endswith("/robots.txt")]

    assert robots_calls == ["https://example.invalid/robots.txt"]


async def test_a_redirecting_robots_txt_is_followed(monkeypatch):
    """
    ratopati.com answers 301 for https://ratopati.com/robots.txt and
    serves its rules (and its Crawl-delay: 20) at the www host.

    A robots.txt that redirects is normal. Reading the 301 as "no
    rules" is the one answer a publisher never meant - it is how a
    stated delay goes missing.
    """
    seen: dict = {}

    async def fake_get(self, url, **kwargs):
        seen["url"] = str(url)
        seen["follow_redirects"] = getattr(self, "follow_redirects", None)
        return FakeResponse(200, ROBOTS_WITH_DELAY)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    checker = RobotsChecker()
    delay = await checker.crawl_delay(
        "https://ratopati.com/feed",
        "NepalNewsMagazineBot/1.0",
    )

    assert delay == 20.0
    assert seen["follow_redirects"] is True


async def test_the_crawl_delay_a_publisher_states_is_read(monkeypatch):
    """
    ratopati.com publishes "Crawl-delay: 20". It is read now.
    """
    fake_get, _ = fake_get_factory(robots_text=ROBOTS_WITH_DELAY)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    checker = RobotsChecker()
    delay = await checker.crawl_delay(
        "https://example.invalid/feed",
        "NepalNewsMagazineBot/1.0",
    )

    assert delay == 20.0


async def test_a_publisher_that_states_no_delay_reads_as_zero(monkeypatch):
    fake_get, _ = fake_get_factory(robots_text=ROBOTS_ALLOW_ALL)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    checker = RobotsChecker()
    delay = await checker.crawl_delay(
        "https://example.invalid/feed",
        "NepalNewsMagazineBot/1.0",
    )

    assert delay == 0.0


# ---------------------------------------------------------------------------
# The budget: one per host, and the publisher's floor
# ---------------------------------------------------------------------------

async def test_the_publishers_crawl_delay_is_the_floor_for_its_budget(monkeypatch):
    """
    A row may say "600 requests a minute"; the publisher says twenty
    seconds. Twenty seconds is what the worker waits.
    """
    fake_get, _ = fake_get_factory(robots_text=ROBOTS_WITH_DELAY)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for(rate_limit_per_minute=600)

    await adapter.check_allowed("https://example.invalid/feed")

    assert adapter.rate_limiter.crawl_delay == 20.0
    assert adapter.rate_limiter.interval == 20.0


async def test_a_publisher_that_states_no_delay_leaves_the_rows_own_rate_alone(monkeypatch):
    fake_get, _ = fake_get_factory(robots_text=ROBOTS_ALLOW_ALL)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for(rate_limit_per_minute=600)

    await adapter.check_allowed("https://example.invalid/feed")

    assert adapter.rate_limiter.crawl_delay == 0.0
    assert adapter.rate_limiter.interval == pytest.approx(0.1)


async def test_the_crawl_delay_can_be_turned_off(monkeypatch):
    """
    An operator may override the publisher - but it has to be asked for.
    """
    fake_get, _ = fake_get_factory(robots_text=ROBOTS_WITH_DELAY)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for(rate_limit_per_minute=600)
    adapter.settings = settings_stub(respect_robots_crawl_delay=False)

    await adapter.check_allowed("https://example.invalid/feed")

    assert adapter.rate_limiter.crawl_delay == 0.0


def test_two_rows_for_one_host_share_one_budget():
    """
    The budget belongs to the publisher, not to the row.
    """
    first = host_budget("example.invalid", 600)
    second = host_budget("example.invalid", 600)

    assert first is second


def test_the_stricter_of_two_rates_for_one_host_wins():
    """
    Two rows, one host: the slower rate is the one that counts.
    """
    lenient = host_budget("example.invalid", 600)
    strict = host_budget("example.invalid", 3)

    assert strict is lenient
    assert lenient.interval == pytest.approx(20.0)


def test_different_hosts_do_not_share_a_budget():
    assert host_budget("one.invalid", 600) is not host_budget("two.invalid", 600)


async def test_the_budget_makes_the_worker_wait_between_two_polls(monkeypatch):
    """
    Two polls of one host: the second one waits.

    Before, the budget was rebuilt for every poll, so the second poll
    started from zero and the publisher never saw the sum of what we
    asked of it.
    """
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)

    adapter = adapter_for(rate_limit_per_minute=600)

    await adapter.rate_limiter.wait()
    await adapter.rate_limiter.wait()

    assert slept, "the second request to a host must wait"
    assert slept[0] == pytest.approx(0.1, abs=0.01)


def test_the_host_a_row_talks_to_is_taken_from_its_urls():
    row = {
        "feed_url": "https://example.invalid/feed",
        "base_url": "https://example.invalid",
    }
    assert source_host(row) == "example.invalid"

    row = {
        "feed_url": None,
        "fallback_url": "https://fallback.invalid/news",
    }
    assert source_host(row) == "fallback.invalid"

    assert source_host({}) == ""


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------

async def test_a_feed_request_carries_a_language_and_the_outlets_own_referer(monkeypatch):
    """
    What an ordinary reader sends, and nothing more.
    """
    captured: dict = {}

    async def fake_get(self, url, **kwargs):
        captured["url"] = str(url)
        captured["headers"] = kwargs.get("headers") or {}
        if str(url).endswith("/robots.txt"):
            return FakeResponse(200, ROBOTS_ALLOW_ALL)
        return FakeResponse(200, FEED)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for(site_url="https://example.invalid")

    await adapter.fetch()

    headers = captured["headers"]

    assert headers["Accept-Language"] == "ne,en;q=0.8"
    assert headers["Referer"] == "https://example.invalid"


async def test_a_feed_request_still_identifies_itself_honestly(monkeypatch):
    """
    The User-Agent is the one we configured - the one that names us and
    gives an address to write to.

    A browser User-Agent would be a lie told to a publisher that has
    already refused us, and it is not needed: the refusals these five
    outlets made were made to requests that ALREADY carried a full
    Chrome User-Agent (Ekantipur's listing adapter sends one), and to
    requests from a client that works perfectly well with ours.
    """
    captured: dict = {}

    async def fake_get(self, url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        if str(url).endswith("/robots.txt"):
            return FakeResponse(200, ROBOTS_ALLOW_ALL)
        return FakeResponse(200, FEED)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for()

    await adapter.fetch()

    assert captured["headers"]["User-Agent"] == (
        "NepalNewsMagazineBot/1.0 (+https://example.test)"
    )


async def test_a_feed_request_without_a_site_url_sends_no_referer(monkeypatch):
    captured: dict = {}

    async def fake_get(self, url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        if str(url).endswith("/robots.txt"):
            return FakeResponse(200, ROBOTS_ALLOW_ALL)
        return FakeResponse(200, FEED)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    adapter = adapter_for(site_url=None, base_url=None)

    await adapter.fetch()

    assert "Referer" not in captured["headers"]


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

#: The migration is found by NAME, not by number.
#:
#: Migrations are numbered in the order they are written, and other work
#: lands in this directory too, so the number of this one is not fixed.
#: What matters is that the file that asks these five publishers less
#: often is present and says what it must say.
MIGRATION_GLOB = "*_ask_the_refused_publishers_less_often.sql"


def migration_0030() -> str:
    files = sorted(MIGRATIONS.glob(MIGRATION_GLOB))
    if not files:
        pytest.skip("the politeness migration is not in this checkout")
    return files[-1].read_text(encoding="utf-8")


def test_the_migration_asks_the_five_publishers_less_often():
    """
    The five outlets that refused us are asked LESS, not more, and on
    the URL that costs one request instead of two.
    """
    sql = migration_0030()

    for name in (
        "Nepal News",
        "Nepal News English",
        "Ratopati",
        "Ujyaalo Online",
        "Ekantipur",
    ):
        assert f"where name = '{name}'" in sql, name

    # The intervals the migration sets, and the ones it replaces.
    assert "set poll_interval_seconds = 300" in sql
    assert "set poll_interval_seconds = 900" in sql

    # Every one of them is asked for a canonical URL, so no poll pays
    # for a redirect first.
    assert "'https://nepalnews.com/feed/'" in sql
    assert "'https://english.nepalnews.com/feed/'" in sql
    assert "'https://www.ratopati.com/feed'" in sql
    assert "'https://www.ujyaaloonline.com/feed'" in sql


def test_the_migration_does_not_disable_an_outlet():
    """
    All five have a working feed (or, for Ekantipur, a working listing)
    from an ordinary client, so none of them is switched off.
    """
    sql = migration_0030()

    assert "enabled = false" not in sql
    assert "allowed = false" not in sql


def test_the_migration_slows_every_row_it_touches():
    """
    No row in this migration is polled FASTER than it was.

    Parsed out of the SQL rather than trusted from a comment: a
    comment can go stale, an UPDATE cannot.
    """
    sql = migration_0030()

    blocks = re.findall(
        r"update sources\s+(.*?)\s+where name = '([^']+)';",
        sql,
        flags=re.DOTALL | re.IGNORECASE,
    )

    assert blocks, "the migration updates no rows at all"

    for body, name in blocks:
        match = re.search(r"poll_interval_seconds\s*=\s*(\d+)", body)
        assert match, f"{name}: no poll interval in the update"

        new_interval = int(match.group(1))
        old_interval = {
            "Nepal News": 90,
            "Nepal News English": 90,
            "Ratopati": 90,
            "Ujyaalo Online": 180,
            "Ekantipur": 300,
        }[name]

        assert new_interval > old_interval, (
            f"{name}: {old_interval}s -> {new_interval}s is not slower"
        )
