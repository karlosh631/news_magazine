"""
Read a page the worker cannot reach from where it stands.

THE RUN THAT COULD NOT READ NINE PUBLISHERS
-------------------------------------------
Nine Nepali publishers answer the CI runner with HTTP 403 and answer an
ordinary client with a 200 - with the worker's own User-Agent, with a
browser's, and with the site's own Referer. Their robots.txt permits
every path we ask for. The refusal is aimed at the ADDRESS.

So those nine contributed nothing at all: every pass, all nine fetched
zero articles. And the pictures of the stories they had published
before the refusal began could never be proven, because the page that
declares them (an og:image, and a JSON-LD NewsArticle block) could not
be read from here.

These specs pin the reader: it is a FALLBACK, it never carries a
credential, and it never raises.
"""

from __future__ import annotations

import httpx
import pytest

from app.sources.reader_proxy import (
    BLOCKED_STATUS_CODES,
    fetch_via_reader,
    fetch_with_reader_fallback,
    reader_proxy_url,
)

PAGE = "<html><head><meta property='og:image' content='https://x.test/a.jpg'></head></html>"


class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class FakeClient:
    """Answers the direct fetch and the reader fetch differently."""

    def __init__(self, direct: FakeResponse, reader: FakeResponse | None = None):
        self.direct = direct
        self.reader = reader
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, headers=None, timeout=None, follow_redirects=None):
        self.calls.append((url, headers or {}))
        if "r.jina.ai" in url or "/https://" in url:
            if self.reader is None:
                raise httpx.ConnectError("the reader is down")
            return self.reader
        return self.direct


class TestTheReader:
    async def test_a_refused_page_is_read_through_the_reader(self):
        client = FakeClient(
            FakeResponse(403), FakeResponse(200, PAGE)
        )

        text, status = await fetch_with_reader_fallback(
            "https://publisher.test/story", client, {"User-Agent": "bot"}
        )

        assert text == PAGE
        assert status == 0
        # The publisher was asked FIRST.
        assert client.calls[0][0] == "https://publisher.test/story"
        # And the reader was asked second.
        assert "r.jina.ai" in client.calls[1][0]

    async def test_a_page_that_answers_is_not_sent_to_the_reader(self):
        client = FakeClient(FakeResponse(200, PAGE))

        text, status = await fetch_with_reader_fallback(
            "https://publisher.test/story", client, {"User-Agent": "bot"}
        )

        assert text == PAGE
        assert status == 200
        assert len(client.calls) == 1

    async def test_a_missing_page_is_not_sent_to_the_reader(self):
        # A 404 is not a refusal. The page is gone; reading it from
        # somewhere else would only find the same 404, and the reader
        # would be spending a request for nothing.
        client = FakeClient(FakeResponse(404))

        text, status = await fetch_with_reader_fallback(
            "https://publisher.test/gone", client, {"User-Agent": "bot"}
        )

        assert text is None
        assert status == 404
        assert len(client.calls) == 1

    async def test_a_reader_that_fails_is_not_a_crash(self):
        client = FakeClient(FakeResponse(403))

        text, status = await fetch_with_reader_fallback(
            "https://publisher.test/story", client, {"User-Agent": "bot"}
        )

        assert text is None
        assert status == 0

    async def test_the_reader_is_asked_for_the_raw_html(self):
        client = FakeClient(FakeResponse(403), FakeResponse(200, PAGE))

        await fetch_via_reader("https://publisher.test/story", client)

        headers = client.calls[0][1]
        assert headers["x-respond-with"] == "html"

    async def test_only_public_addresses_are_read(self):
        client = FakeClient(FakeResponse(200, PAGE))

        assert await fetch_via_reader("file:///etc/passwd", client) is None
        assert await fetch_via_reader("", client) is None
        assert client.calls == []

    def test_the_readers_address_is_built_correctly(self):
        assert (
            reader_proxy_url("https://r.jina.ai", "https://p.test/a")
            == "https://r.jina.ai/https://p.test/a"
        )
        assert (
            reader_proxy_url("https://r.jina.ai/", "https://p.test/a")
            == "https://r.jina.ai/https://p.test/a"
        )

    def test_the_refusal_codes_are_the_ones_measured(self):
        # 403 is the one these nine answer with. 429 and 451 are the
        # other two ways a publisher says "not from here".
        for status in (403, 429, 451):
            assert status in BLOCKED_STATUS_CODES
        assert 404 not in BLOCKED_STATUS_CODES
        assert 500 not in BLOCKED_STATUS_CODES
