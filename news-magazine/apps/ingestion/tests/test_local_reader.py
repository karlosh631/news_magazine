"""
The local reader: the tier that cannot expire.

The pipeline reads a page it cannot reach directly through a reader
proxy, and that proxy is a free service with a free-tier ceiling. When
its quota runs out, the page is simply not read — which is a story
with no headline from its own page, no picture proven against it, and
a listing row that has to be thrown away.

The tier below the proxy is a library and a socket: httpx fetches the
HTML, trafilatura reads the article out of it, and neither has a key, a
quota or an expiry.

These tests pin the parts that are ours: the shape of the extraction,
the decision about what counts as an article, and the promise that
nothing here ever raises.
"""

from __future__ import annotations

import httpx
import pytest

from app.sources.local_reader import (
    LocalArticle,
    extract_locally,
    fetch_article,
    fetch_html,
    page_has_article,
)

ARTICLE_HTML = """
<html><head><title>सडक सुरक्षित गर्न के गर्नुपर्छ?</title>
<meta name="description" content="A description of the story.">
</head>
<body>
  <nav>Home About Contact</nav>
  <article>
    <h1>सडक सुरक्षित गर्न के गर्नुपर्छ?</h1>
    <p>काठमाडौंको सडकमा दुर्घटना हुनुको मुख्य कारण भनेको तीव्र गतिमा चल्ने सवारी साधनहरू
    हुन्। ट्राफिक प्रहरीले सडक सुरक्षाका लागि विभिन्न उपायहरू अवलम्बन गरेको छ।</p>
    <p>सडक सुरक्षित गर्न नै सरकारको प्राथमिकता रहेको छ भने नगरपालिकाले पनि
    यसका लागि सम्बन्धित निकायसँग समन्वय गर्दै आएको छ।</p>
    <p>यसका अतिरिक्त सडकमा फोरवार्ड निषेध, चालकको सजगता र सवारी साधनको नियमित
    जाँच गरिने कुरा पनि उल्लेख गरिएको छ।</p>
    <p>सम्बन्धित विभागले आगामी महिनादेखि विशेष अनुगमन सुरु गर्ने भएको छ।</p>
  </article>
  <footer>Copyright</footer>
</body></html>
"""

LISTING_HTML = """
<html><head><title>नवीनतम</title></head>
<body>
  <div class="post"><a href="/a">पहिलो शीर्षक</a></div>
  <div class="post"><a href="/b">दोस्रो शीर्षक</a></div>
</body></html>
"""


def test_an_article_is_read_out_of_its_page() -> None:
    article = extract_locally(ARTICLE_HTML)
    assert article is not None
    assert isinstance(article, LocalArticle)
    assert "सडक" in article.text
    # The navigation and the footer are not the story.
    assert "Copyright" not in article.text
    assert "About Contact" not in article.text


def test_a_page_with_no_article_is_not_one() -> None:
    # A listing page has links and no body. Treating it as an article
    # is how a listing page's own headline ends up as a story's
    # headline, which is the bug the verifier exists to stop.
    assert extract_locally(LISTING_HTML) is None
    assert extract_locally("") is None
    assert extract_locally("   ") is None
    assert extract_locally("<html><body>nothing</body></html>") is None


def test_extraction_never_raises() -> None:
    # Broken markup, a stray null byte, a page that is not a page at
    # all: none of them may take the pass down.
    for html in (
        "<html><body>" + ("x" * 100000) + "</body></html>",
        "<html>\x00<body>text</body></html>",
        "<<<>>>",
        "<html><body>" + "<div>" * 500,
    ):
        assert extract_locally(html) is None or isinstance(
            extract_locally(html), LocalArticle
        )


@pytest.mark.asyncio
async def test_a_refused_page_is_not_a_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_html("https://example.com/story", client) is None
        assert await fetch_article("https://example.com/story", client) is None
        assert await page_has_article("https://example.com/story", client) is False


@pytest.mark.asyncio
async def test_a_refused_page_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_html("https://example.com/story", client) is None
        assert await fetch_article("https://example.com/story", client) is None


@pytest.mark.asyncio
async def test_a_page_is_read_and_recognised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ARTICLE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        html = await fetch_html("https://example.com/story", client)
        assert html is not None
        assert "सडक" in html

        article = await fetch_article("https://example.com/story", client)
        assert article is not None
        assert "सडक" in article.text

        assert await page_has_article("https://example.com/story", client) is True


@pytest.mark.asyncio
async def test_a_listing_page_is_not_recognised_as_an_article() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=LISTING_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await page_has_article("https://example.com/", client) is False


@pytest.mark.asyncio
async def test_a_url_that_is_not_a_url_is_refused() -> None:
    async with httpx.AsyncClient() as client:
        assert await fetch_html("not-a-url", client) is None
        assert await fetch_html("", client) is None
        assert await fetch_html("file:///etc/passwd", client) is None
