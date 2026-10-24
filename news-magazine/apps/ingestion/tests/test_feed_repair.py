"""
A feed that is not valid XML, and a feed the publisher refuses.

THE PRODUCTION ERRORS THESE PIN DOWN
------------------------------------
    Pradesh Post  fetch_or_parse_error
    Unable to parse RSS/Atom feed: <unknown>:9:14: not well-formed
    (invalid token)

    Khabarzone   fetch_or_parse_error
    Client error '403 Forbidden' for url
    'https://www.khabarzone.com/feed?posts_per_page=100'

The first is a byte in the document that XML does not allow - a raw
control character pasted out of a word processor into a headline, a
byte-order mark in the middle of the document, a bare `&`. The
document is not garbage: it carries the outlet's headlines, and the
headlines are the whole point of the pass. So the body is REPAIRED
before it is parsed, and the error is reported only if the repaired
body still fails.

The second is a publisher that refuses this ADDRESS, and a reader
proxy that can reach it from one that has not been refused.
"""

from __future__ import annotations

import feedparser
import httpx
import pytest

from app.sources.generic_rss import (
    GenericRssAdapter,
    _escape_bare_ampersands,
    _parse_feed_document,
    sanitize_xml_text,
)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Pradeshpost</title>
<link>https://pradeshpost.com/</link>
<item>
<title>नेटटिभी, डिगो, एनटीटीभी जस्ता ओटीटी नियमनको दायरामा</title>
<link>https://pradeshpost.com/2026/06/17/1799/</link>
<pubDate>Wed, 17 Jun 2026 08:44:24 +0000</pubDate>
</item>
<item>
<title>दोस्रो पटक बेलायतको राजकीय भ्रमणमा ट्रम्प</title>
<link>https://pradeshpost.com/2026/06/17/1798/</link>
<pubDate>Wed, 17 Jun 2026 08:40:24 +0000</pubDate>
</item>
</channel>
</rss>
"""


# -------------------------------------------------------------------
# The sanitiser
# -------------------------------------------------------------------

def test_a_control_character_is_removed():
    """A vertical tab, a form feed and a NUL are all illegal in XML."""
    dirty = "<title>प्रदेश\x0bpost\x0c\x00</title>"
    clean = sanitize_xml_text(dirty)

    assert "\x0b" not in clean
    assert "\x0c" not in clean
    assert "\x00" not in clean
    # The text around it is untouched.
    assert "प्रदेश" in clean and "post" in clean


def test_tab_newline_and_carriage_return_are_kept():
    """They are legal in XML, and they are the formatting of the feed."""
    text = "one\ttwo\nthree\rfour"
    assert sanitize_xml_text(text) == text


def test_a_byte_order_mark_is_removed_wherever_it_is():
    assert "﻿" not in sanitize_xml_text("﻿<rss/>")
    assert "﻿" not in sanitize_xml_text("<rss>﻿</rss>")


def test_a_bare_ampersand_is_escaped_and_an_entity_is_not():
    assert _escape_bare_ampersands("a & b") == "a &amp; b"
    assert _escape_bare_ampersands("a &amp; b") == "a &amp; b"
    assert _escape_bare_ampersands("a &#8217; b") == "a &#8217; b"
    assert _escape_bare_ampersands("a &nbsp; b") == "a &amp;nbsp; b"


# -------------------------------------------------------------------
# The repair, end to end
# -------------------------------------------------------------------

def test_a_well_formed_feed_is_not_touched():
    parsed, repaired, _original = _parse_feed_document(FEED)

    assert len(parsed.entries) == 2
    assert repaired is False
    assert parsed.bozo is False


def test_a_control_character_in_a_tag_name_costs_every_entry():
    """
    This is the production failure.

    A byte pasted into the middle of a tag name makes the tag no
    longer `<item>`, so feedparser's forgiving parser does not see it
    either: the document goes from two entries to none, and the only
    thing left is expat's "not well-formed (invalid token)".
    """
    broken = FEED.replace("<item>", "<ite\x0bm>")

    raw = feedparser.parse(broken)
    assert raw.bozo
    assert raw.entries == []
    assert "not well-formed" in str(raw.get("bozo_exception"))

    parsed, repaired, _original = _parse_feed_document(broken)

    assert len(parsed.entries) == 2
    assert repaired is True
    assert parsed.bozo is False


def test_a_control_character_in_a_headline_costs_nothing():
    broken = FEED.replace("डिगो", "डिगो\x0b")

    parsed, repaired, _original = _parse_feed_document(broken)

    assert len(parsed.entries) == 2
    assert parsed.bozo is False


def test_a_bare_ampersand_is_repaired():
    broken = FEED.replace(
        "<title>नेटटिभी", "<title>नेटटिभी & डिगो", 1
    )

    parsed, _repaired, _original = _parse_feed_document(broken)

    assert len(parsed.entries) == 2
    assert parsed.bozo is False


def test_a_document_that_cannot_be_repaired_still_raises():
    """
    The repair is a rescue, not a disguise. A document that is not a
    feed at all must still be reported, because storing nothing is
    better than storing an invented story.
    """
    with pytest.raises(ValueError) as excinfo:
        GenericRssAdapter(
            {
                "name": "Broken",
                "feed_url": "https://publisher.test/feed",
                "base_url": "https://publisher.test",
                "allowed": True,
                "enabled": True,
            }
        ).parse("<html><body>this is not a feed at all</body></html>")

    assert "Unable to parse RSS/Atom feed" in str(excinfo.value)


def test_the_adapter_reports_the_error_only_after_the_repair():
    adapter = GenericRssAdapter(
        {
            "name": "Pradesh Post",
            "feed_url": "https://www.pradeshpost.com/rss",
            "base_url": "https://www.pradeshpost.com",
            "allowed": True,
            "enabled": True,
        }
    )

    broken = FEED.replace("<item>", "<ite\x0bm>")
    entries = adapter.parse(broken)

    assert len(entries) == 2
    assert entries[0]["link"] == "https://pradeshpost.com/2026/06/17/1799/"


# -------------------------------------------------------------------
# The feed the publisher refuses
# -------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text
        self.url = "https://publisher.test/feed"


class FakeClient:
    """Refuses the publisher, and answers the reader."""

    def __init__(self, direct_status=403, reader_text=None, local_text=None):
        self.direct_status = direct_status
        self.reader_text = reader_text
        self.local_text = local_text
        self.calls: list[str] = []

    async def get(self, url, headers=None, timeout=None, follow_redirects=None):
        self.calls.append(str(url))
        if "r.jina.ai" in str(url):
            if self.reader_text is None:
                raise httpx.ConnectError("the reader is down")
            return FakeResponse(200, self.reader_text)
        return FakeResponse(self.direct_status, "Forbidden")


def _adapter():
    return GenericRssAdapter(
        {
            "name": "Khabarzone",
            "feed_url": "https://www.khabarzone.com/feed",
            "base_url": "https://www.khabarzone.com",
            "site_url": "https://www.khabarzone.com",
            "allowed": True,
            "enabled": True,
            "rate_limit_per_minute": 6,
        }
    )


async def test_a_refused_feed_is_read_through_the_reader(monkeypatch):
    adapter = _adapter()
    client = FakeClient(reader_text=FEED)

    text = await adapter._read_refused_feed(
        "https://www.khabarzone.com/feed?posts_per_page=100", 403, client, {}
    )

    assert text == FEED
    # The publisher was asked first, the reader second.
    assert "r.jina.ai" in client.calls[-1]


async def test_the_feed_without_its_query_string_is_tried_first(monkeypatch):
    """
    The parameterised URL is a parameter the publisher never asked for.
    A publisher that refuses it and serves the plain one is not
    unusual.
    """
    adapter = _adapter()

    class BareWorks(FakeClient):
        async def get(self, url, headers=None, timeout=None, follow_redirects=None):
            self.calls.append(str(url))
            if "r.jina.ai" in str(url):
                raise httpx.ConnectError("the reader is down")
            if "?" not in str(url):
                return FakeResponse(200, FEED)
            return FakeResponse(403, "Forbidden")

    client = BareWorks()

    text = await adapter._read_refused_feed(
        "https://www.khabarzone.com/feed?posts_per_page=100", 403, client, {}
    )

    assert text == FEED
    # The reader was never asked.
    assert not any("r.jina.ai" in call for call in client.calls)


async def test_a_feed_we_cannot_read_at_all_is_reported_not_invented(monkeypatch):
    adapter = _adapter()
    client = FakeClient()

    text = await adapter._read_refused_feed(
        "https://www.khabarzone.com/feed?posts_per_page=100", 403, client, {}
    )

    assert text is None
