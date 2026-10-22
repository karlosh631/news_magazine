"""
The listing adapter's two rules that were wrong.

Both of them were found by an operator's dashboard, not by a test: the
error log said "No headlines found for 'X'. The selector_config in the
sources table is probably stale", and the operator's answer was that
the selector was fine. It was, in one case. In the other, the adapter
crashed on the first card.
"""

from __future__ import annotations

import re

import pytest

from app.sources.html_listing import HtmlListingAdapter, _HAS_HEADLINE_RE


def _adapter() -> HtmlListingAdapter:
    return HtmlListingAdapter.__new__(HtmlListingAdapter)


def _card(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").select_one("article")


# -------------------------------------------------------------------
# The `date` key
# -------------------------------------------------------------------


def test_a_selector_config_without_a_date_key_does_not_crash():
    """
    The `date` key is documented as optional, and it used to be fatal.

    The guard read

        if config.get("date", "time"):
            card.select_one(config["date"])

    `config.get("date", "time")` returns "time" when the key is
    ABSENT, so the guard was always true and the next line raised
    `KeyError: 'date'` on the first card of the listing. Every outlet
    whose selector_config did not carry a `date` key failed with a
    crash that the operator saw as a broken selector.
    """
    adapter = _adapter()
    card = _card(
        '<article><h3><a href="/news/story">A proper headline long enough to pass</a></h3>'
        '<img src="/pic.jpg"><time datetime="2026-10-22T10:00:00Z">'
        "22 October</time></article>"
    )

    row = adapter._parse_card(
        card,
        {
            "item": "article",
            "link": "h3 a[href]",
            "title": "h3",
            "image": "img",
            "exclude": r"/author|/tag/",
        },
        "https://example.test/",
        re.compile(r"/author|/tag/"),
    )

    assert row is not None
    assert row["title"] == "A proper headline long enough to pass"
    assert row["link"] == "https://example.test/news/story"


def test_a_selector_config_with_a_date_key_still_reads_the_date():
    adapter = _adapter()
    card = _card(
        '<article><h3><a href="/news/story">A proper headline long enough to pass</a></h3>'
        '<time datetime="2026-10-22T10:00:00Z">22 October</time></article>'
    )

    row = adapter._parse_card(
        card,
        {
            "item": "article",
            "link": "h3 a[href]",
            "title": "h3",
            "date": "time",
            "exclude": r"/author|/tag/",
        },
        "https://example.test/",
        re.compile(r"/author|/tag/"),
    )

    assert row is not None
    assert row["date"] == "2026-10-22T10:00:00Z"


def test_an_empty_date_selector_is_allowed_and_means_no_date():
    """
    A `date` of "" is how an operator says "this outlet does not put a
    date on its cards". It must not fall back to the default.
    """
    adapter = _adapter()
    card = _card(
        '<article><h3><a href="/news/story">A proper headline long enough to pass</a></h3>'
        '<time datetime="2026-10-22T10:00:00Z">22 October</time></article>'
    )

    row = adapter._parse_card(
        card,
        {
            "item": "article",
            "link": "h3 a[href]",
            "title": "h3",
            "date": "",
            "exclude": r"/author|/tag/",
        },
        "https://example.test/",
        re.compile(r"/author|/tag/"),
    )

    assert row is not None
    assert row["date"] is None


# -------------------------------------------------------------------
# The headline check
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><h1>The headline of the story</h1></body></html>",
        "<html><body><h2 class='title'>The headline of the story</h2></body></html>",
        '<html><head><meta property="og:title" content="The headline">'
        "</head><body></body></html>",
        '<html><head><meta name="twitter:title" content="The headline">'
        "</head><body></body></html>",
    ],
)
def test_a_page_that_names_the_story_is_an_article(html):
    """
    The check is "does this page name the story", and there is more
    than one way to answer it.

    An outlet whose article pages carry the headline in an `<h2>` or
    only in the Open Graph metadata was rejected in its entirety -
    every candidate link raised "did not resolve to an article page",
    which reads exactly like a broken selector and is not one.
    """
    assert _HAS_HEADLINE_RE.search(html), html


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><p>Just a paragraph with no headline anywhere.</p>"
        "<div>and some navigation</div></body></html>",
        "<html><body><h3>A subheading is not the headline</h3></body></html>",
    ],
)
def test_a_page_with_no_headline_is_not_an_article(html):
    assert not _HAS_HEADLINE_RE.search(html), html
