"""
The listing verifier, and the three ways a probe can end.

THE PRODUCTION ERRORS THESE PIN DOWN
------------------------------------
    Nepal Patra          22/36 candidate links did not resolve to an
                         article page. Refusing to store 22 broken rows.
    Hamro Patro National 34/34 candidate links did not resolve to an
                         article page.

Both publishers were thrown away by the verifier, and both were
thrown away for the same reason: the reader proxy is the only way to
read an article page on a host that refuses this address, and the
reader is rate-limited. When it answered "slow down", every candidate
was counted as a BROKEN LINK - a link that had been read and named no
story - and the outlet was dropped.

A page we could not read is not a broken link. So the probe now has
three answers, and only one of them is evidence:

    article  the page was read and named a story
    broken   the page was read and named no story
    unknown  the page could not be read at all

and only "broken" is allowed to throw an outlet away.
"""

from __future__ import annotations

import httpx
import pytest

import app.sources.html_listing as html_listing
from app.sources.html_listing import HtmlListingAdapter

ARTICLE = (
    "<html><head><meta property='og:title' content='The headline of the "
    "story'></head><body><article>" + ("The body of the story. " * 40) +
    "</article></body></html>"
)

SHELL = (
    "<html><head><title>Loading</title></head><body><div id='root'></div>"
    "</body></html>"
)

LISTING = """
<html><body>
  <article><h3><a href="/news/one">The first story's headline here</a></h3></article>
  <article><h3><a href="/news/two">The second story's headline here</a></h3></article>
  <article><h3><a href="/news/three">The third story's headline here</a></h3></article>
  <article><h3><a href="/news/four">The fourth story's headline here</a></h3></article>
</body></html>
"""


def adapter() -> HtmlListingAdapter:
    return HtmlListingAdapter(
        {
            "name": "Hamro Patro National",
            "base_url": "https://www.hamropatro.com",
            "fallback_url": "https://www.hamropatro.com/news/national",
            "site_url": "https://www.hamropatro.com/news/national",
            "adapter_key": "html_listing",
            "enabled": True,
            "allowed": True,
            "rate_limit_per_minute": 3,
            "default_language": "ne",
            "selector_config": {
                "item": "a.hp-card-surface[href*='/news/detail/']",
                "link": "a[href]",
                "title": "h3",
                "image": "img",
                "exclude": r"/author/|/category/",
            },
        }
    )


class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text
        self.url = "https://www.hamropatro.com/news/detail/one"


class FakeClient:
    """Answers the publisher, the reader and the local tier differently."""

    def __init__(self, publisher=403, reader=ARTICLE, publisher_text="Forbidden"):
        self.publisher = publisher
        self.publisher_text = publisher_text
        self.reader = reader
        self.publisher_calls: list[str] = []
        self.reader_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, timeout=None, follow_redirects=None):
        url = str(url)
        if "r.jina.ai" in url:
            self.reader_calls.append(url)
            if self.reader is None:
                raise httpx.ConnectError("the reader is down")
            return FakeResponse(200, self.reader)
        self.publisher_calls.append(url)
        return FakeResponse(self.publisher, self.publisher_text)


@pytest.fixture
def fake_client(monkeypatch):
    """Install a fake client into the adapter's probe."""
    holder: dict[str, FakeClient] = {}

    def install(client: FakeClient):
        holder["client"] = client
        monkeypatch.setattr(
            html_listing.httpx, "AsyncClient", lambda *a, **k: client
        )
        return client

    yield install


def entries(count: int = 4) -> list[dict]:
    return [
        {"link": f"https://www.hamropatro.com/news/detail/{i}",
         "title": f"The headline of story {i} here"}
        for i in range(count)
    ]


# -------------------------------------------------------------------
# The three answers
# -------------------------------------------------------------------

async def test_a_refused_page_read_through_the_reader_is_an_article(
    fake_client, monkeypatch
):
    client = fake_client(FakeClient(publisher=403, reader=ARTICLE))

    kept = await adapter().verify_entries(entries(4))

    assert len(kept) == 4


async def test_a_refused_page_the_reader_cannot_fetch_is_kept(
    fake_client, monkeypatch
):
    """
    The reader said "slow down". We know nothing about the link, so the
    link is KEPT - and the outlet is not thrown away.
    """
    client = fake_client(FakeClient(publisher=403, reader=None))

    kept = await adapter().verify_entries(entries(4))

    assert len(kept) == 4


async def test_a_page_that_is_gone_is_dropped(fake_client, monkeypatch):
    """A 404 is a real answer about the page."""
    client = fake_client(FakeClient(publisher=404))

    with pytest.raises(ValueError) as excinfo:
        await adapter().verify_entries(entries(4))

    assert "did not resolve to an article page" in str(excinfo.value)


async def test_a_page_with_no_headline_is_dropped(fake_client, monkeypatch):
    client = fake_client(FakeClient(publisher=200, reader=None))

    # The direct read answers 200 with a shell: no headline, no body.
    monkeypatch.setattr(
        html_listing,
        "_HAS_HEADLINE_RE",
        type("R", (), {"search": staticmethod(lambda text: None)}),
    )

    with pytest.raises(ValueError):
        await adapter().verify_entries(entries(4))


async def test_the_reader_is_not_asked_twice_for_the_same_page(
    fake_client, monkeypatch
):
    """
    One read per candidate, not two.

    The probe used to read a refused page through the reader, and then
    - because the reader's text had no headline marker - ask the reader
    again for the very same URL. That doubled the cost of exactly the
    probes that are the most expensive.
    """
    client = fake_client(FakeClient(publisher=403, reader=SHELL))

    with pytest.raises(ValueError):
        await adapter().verify_entries(entries(4))

    assert len(client.reader_calls) == 4


async def test_a_page_with_a_body_is_an_article_even_without_a_marker(
    fake_client, monkeypatch
):
    """
    The local tier, and it costs no request: we are holding the page.

    An outlet whose article pages carry their headline somewhere no tag
    named used to be rejected in its entirety - every candidate raised
    "did not resolve to an article page", which reads exactly like a
    broken selector and is not one.
    """
    client = fake_client(
        FakeClient(publisher=200, reader=None, publisher_text=ARTICLE)
    )

    monkeypatch.setattr(
        html_listing,
        "_HAS_HEADLINE_RE",
        type("R", (), {"search": staticmethod(lambda text: None)}),
    )

    kept = await adapter().verify_entries(entries(4))

    assert len(kept) == 4


# -------------------------------------------------------------------
# What the operator sees
# -------------------------------------------------------------------

def test_the_marker_is_the_one_the_operator_was_told_about():
    """
    The marker the verifier recognises.

    `<h1>` is where a headline usually lives, but it is not the only
    place, and an outlet whose article pages carry the headline in an
    `<h2>`, or only in the Open Graph metadata, was rejected in its
    entirety.
    """
    pattern = html_listing._HAS_HEADLINE_RE.pattern

    assert "<h1" in pattern
    assert "<h2" in pattern
    assert "og:title" in pattern
    assert "twitter:title" in pattern

    for page in (
        "<html><body><h1>The headline of the story</h1></body></html>",
        "<html><body><h2 class='title'>The headline of the story</h2></body></html>",
        "<html><head><meta property='og:title' content='The headline'>"
        "</head><body></body></html>",
        "<html><head><meta name='twitter:title' content='The headline'>"
        "</head><body></body></html>",
    ):
        assert html_listing._HAS_HEADLINE_RE.search(page), page
