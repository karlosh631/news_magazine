"""
RSS feed paging.

THE PROBLEM
-----------
A WordPress feed serves a fixed number of items: the site's
"posts_per_rss" option. That is 10 for most WordPress sites and 5
for several Nepali ones, so a poll of a Nepali outlet on its own
WordPress feed sees five stories however often it polls.

WHAT WAS MEASURED, AND WHY THE CODE LOOKS LIKE THIS
---------------------------------------------------
The obvious fix -- "ask for more with ?posts_per_page=100" -- does
NOT work, and the code below says so in a comment rather than
pretending otherwise. Against the live Nepali feeds on 2026-10-22:

    ?posts_per_page=100   the item count and the first item are
                           IDENTICAL with and without it, on all ten
                           WordPress feeds tested. WordPress pins the
                           main feed's posts_per_page to the
                           posts_per_rss option and ignores the
                           query string.

    ?paged=2, ?paged=3     these DO work. They return the next page
                           of items -- older stories, the same count
                           per page. This is the only mechanism
                           measured that gets more items out of a
                           WordPress feed.

So the page size is still sent (it is free, and it helps any site
that does honour it) and paging is the mechanism that actually
delivers. Paging is OFF by default because every extra page is one
extra request to a publisher, and being a polite client is this
codebase's first rule.

WHAT IS ASSERTED HERE
---------------------
Not that a number changed -- that is a claim about the web, not about
this code. What is asserted is that:

  * the URL is built correctly, including the "&" vs "?" rule
  * a host that is not a known WordPress install is NEVER given a
    parameter, whatever the settings say
  * a feed that is not WordPress is fetched exactly as it was
  * the settings are real settings, readable from the environment
  * several pages are merged, and a story on two pages is kept once
  * one malformed page does not lose the pages that did work
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.sources.generic_rss import (
    KNOWN_WORDPRESS_HOSTS,
    MAX_FEED_PAGES,
    GenericRssAdapter,
    build_feed_urls,
)


def settings(**overrides) -> SimpleNamespace:
    """A settings object with the paging knobs and nothing else that matters."""
    base = {
        "rss_page_size": 100,
        "rss_page_size_param": "posts_per_page",
        "rss_feed_pages": 1,
        "rss_wordpress_hosts": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# The URL that gets built
# ---------------------------------------------------------------------------

def test_page_size_is_appended_to_a_wordpress_feed():
    urls = build_feed_urls("https://www.khabarzone.com/feed", settings())
    assert urls == ["https://www.khabarzone.com/feed?posts_per_page=100"]


def test_a_feed_that_is_not_wordpress_is_left_alone():
    """
    The most important assertion in this file.

    A non-WordPress server is not asked for anything it cannot
    answer, and its URL -- which the operator chose deliberately --
    is not rewritten.
    """
    url = "https://www.ratopati.com/feed"
    assert build_feed_urls(url, settings()) == [url]
    assert build_feed_urls(url, settings(rss_feed_pages=3)) == [url]


def test_a_url_with_its_own_query_string_is_extended_with_an_ampersand():
    url = "https://www.khabarzone.com/feed?post_type=post"
    urls = build_feed_urls(url, settings())
    assert urls == [
        "https://www.khabarzone.com/feed?post_type=post&posts_per_page=100"
    ]
    # The original query string survives, and only one "?" appears.
    assert urls[0].count("?") == 1
    assert "post_type=post" in urls[0]


def test_paging_adds_one_url_per_page():
    urls = build_feed_urls(
        "https://www.khabarzone.com/feed",
        settings(rss_feed_pages=3),
    )
    assert urls == [
        "https://www.khabarzone.com/feed?posts_per_page=100",
        "https://www.khabarzone.com/feed?posts_per_page=100&paged=2",
        "https://www.khabarzone.com/feed?posts_per_page=100&paged=3",
    ]


def test_paging_a_url_that_already_has_a_query_string():
    urls = build_feed_urls(
        "https://www.khabarzone.com/feed?post_type=post",
        settings(rss_feed_pages=2),
    )
    assert urls == [
        "https://www.khabarzone.com/feed?post_type=post&posts_per_page=100",
        "https://www.khabarzone.com/feed?post_type=post&posts_per_page=100&paged=2",
    ]


def test_page_size_of_zero_asks_for_nothing():
    urls = build_feed_urls(
        "https://www.khabarzone.com/feed",
        settings(rss_page_size=0),
    )
    assert urls == ["https://www.khabarzone.com/feed"]


def test_one_page_is_the_default_and_changes_nothing():
    """
    The default must be today's behaviour: one request, one URL.
    """
    urls = build_feed_urls("https://www.khabarzone.com/feed", settings())
    assert len(urls) == 1


def test_the_number_of_pages_is_capped():
    """
    A misconfigured setting must not turn a poll into a crawl.
    """
    urls = build_feed_urls(
        "https://www.khabarzone.com/feed",
        settings(rss_feed_pages=1000),
    )
    assert len(urls) == MAX_FEED_PAGES


def test_the_operator_can_add_a_wordpress_host_without_a_deploy():
    urls = build_feed_urls(
        "https://example.invalid/feed",
        settings(rss_wordpress_hosts="Example.invalid, other.invalid"),
    )
    assert urls == ["https://example.invalid/feed?posts_per_page=100"]


def test_the_known_wordpress_hosts_are_real_hosts():
    """
    A guard against a typo turning a host list into a set of names
    that can never match.
    """
    assert KNOWN_WORDPRESS_HOSTS
    for host in KNOWN_WORDPRESS_HOSTS:
        assert "." in host
        assert " " not in host
        assert host == host.lower()


# ---------------------------------------------------------------------------
# The settings are real settings
# ---------------------------------------------------------------------------

def test_the_paging_settings_are_readable_from_the_environment(monkeypatch):
    monkeypatch.setenv("RSS_PAGE_SIZE", "250")
    monkeypatch.setenv("RSS_FEED_PAGES", "4")
    monkeypatch.setenv("RSS_PAGE_SIZE_PARAM", "limit")

    configured = Settings(
        supabase_url="http://localhost",
        supabase_service_role_key="x",
        ingestion_secret="x",
        cron_secret="x",
    )

    assert configured.rss_page_size == 250
    assert configured.rss_feed_pages == 4
    assert configured.rss_page_size_param == "limit"


def test_the_default_is_one_page():
    configured = Settings(
        supabase_url="http://localhost",
        supabase_service_role_key="x",
        ingestion_secret="x",
        cron_secret="x",
    )
    assert configured.rss_feed_pages == 1


# ---------------------------------------------------------------------------
# Merging the pages
# ---------------------------------------------------------------------------

def _feed(items: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>Wed, 21 Oct 2026 10:00:00 +0000</pubDate></item>"
        for title, link in items
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>"


def _adapter(documents: list[str]) -> GenericRssAdapter:
    adapter = GenericRssAdapter({
        "name": "Test",
        "feed_url": "https://example.invalid/feed",
        "allowed": True,
        "rate_limit_per_minute": 6,
        "default_language": "ne",
        "republish_permission": False,
    })
    adapter._documents = documents
    return adapter


def test_several_pages_are_merged_into_one_list():
    adapter = _adapter([
        _feed([("A", "https://example.invalid/a")]),
        _feed([("B", "https://example.invalid/b")]),
    ])

    entries = adapter.parse(adapter._documents[0])

    assert [e["title"] for e in entries] == ["A", "B"]


def test_a_story_on_two_pages_is_kept_once():
    adapter = _adapter([
        _feed([("A", "https://example.invalid/a")]),
        _feed([("A", "https://example.invalid/a"), ("B", "https://example.invalid/b")]),
    ])

    entries = adapter.parse(adapter._documents[0])

    assert [e["title"] for e in entries] == ["A", "B"]


def test_one_malformed_page_does_not_lose_the_pages_that_worked():
    adapter = _adapter([
        _feed([("A", "https://example.invalid/a")]),
        "not xml at all",
    ])

    entries = adapter.parse(adapter._documents[0])

    assert [e["title"] for e in entries] == ["A"]


def test_a_whole_feed_that_cannot_be_parsed_still_raises():
    adapter = _adapter(["not xml at all"])

    with pytest.raises(ValueError):
        adapter.parse(adapter._documents[0])


def test_an_entry_with_no_title_is_dropped_by_normalize():
    """
    feedparser recovers a truncated item as an entry with no fields
    at all. It survives parse(), and normalize() is what throws it
    away, so the pipeline never sees a headline-less article.
    """
    adapter = _adapter(["<rss><channel><item><title>broken"])

    entries = adapter.parse(adapter._documents[0])
    articles = adapter.normalize(entries)

    assert articles == []


def test_a_well_formed_but_empty_feed_returns_nothing():
    """
    Not an error. An empty feed is an empty feed.
    """
    adapter = _adapter([
        "<?xml version='1.0'?><rss version='2.0'><channel></channel></rss>"
    ])

    assert adapter.parse(adapter._documents[0]) == []


def test_a_single_document_behaves_exactly_as_before():
    """
    One document, no paging: the original behaviour, unchanged.
    """
    adapter = _adapter([_feed([("A", "https://example.invalid/a")])])
    entries = adapter.parse(adapter._documents[0])
    assert len(entries) == 1
    assert entries[0]["title"] == "A"


def test_parse_works_without_a_fetch():
    """
    parse() is called with a raw string in tests and by anything that
    hands the adapter a document it did not fetch. It must still work.
    """
    adapter = GenericRssAdapter({
        "name": "Test",
        "feed_url": "https://example.invalid/feed",
        "allowed": True,
        "rate_limit_per_minute": 6,
        "default_language": "ne",
    })

    entries = adapter.parse(_feed([("A", "https://example.invalid/a")]))

    assert [e["title"] for e in entries] == ["A"]
