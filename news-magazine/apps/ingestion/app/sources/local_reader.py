"""
Read a page ourselves, with no third party in the middle.

THE PROBLEM THIS SOLVES
-----------------------
The pipeline reads a page it cannot reach directly through a reader
proxy (`sources/reader_proxy.py`). That proxy is a free service with a
free-tier ceiling: twenty pages a minute without a key, sixty with
one, and when its quota runs out it answers "slow down" and the page
is simply not read.

A page that is not read is a story with no headline from its own page,
no picture proven against it, and a listing row that has to be thrown
away. So the pipeline's health depends on a service the operator does
not control, which is exactly the kind of dependency this system is
supposed not to have.

THE TIER THAT CANNOT EXPIRE
---------------------------
A page is, underneath, an HTTP response and some text. Both of those
can be had locally, with no key, no quota and no third party:

    httpx        fetches the raw HTML, with whatever headers we like
    trafilatura  reads the article out of that HTML, on this machine

`trafilatura` is already a dependency of this service (it is what
extracts the body of every article that is read), so this costs
nothing to add and nothing to keep: it is a library, not a service.
It cannot be exhausted, it cannot expire, and it has no key.

WHAT IT IS AND IS NOT FOR
-------------------------
It is a FALLBACK, and it is the last one. The order is always:

    1. ask the publisher, directly
    2. ask the reader proxy
    3. read it ourselves, here, on this machine

The third tier cannot do what the second one does, and the difference
matters: a publisher that refuses THIS ADDRESS refuses us too, and no
amount of local cleverness will make it answer. What it can do is
everything the reader proxy was being used for that is not really
about the refusal:

    - PROVING that a candidate link is an article page. The verifier
      used to need the reader proxy to confirm a page had a headline
      and a body. Locally, trafilatura answers that question for the
      price of one request and no quota at all.
    - EXTRACTING the article text of a page we CAN reach, when the
      site's own extractor found nothing. A better extractor on a page
      we already have is free.
    - READING a feed that the publisher answers but that is malformed
      enough for our own parser to reject.

So the pipeline keeps working when the reader proxy is gone, and it
loses only what the reader proxy was uniquely able to do: reach a
publisher that refuses this address.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

try:  # pragma: no cover - the dependency is in requirements.txt
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: The headers a publisher is most likely to answer.
#:
#: Some of them refuse a plain bot User-Agent and answer a browser's.
#: This is the same set the rest of the pipeline sends.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ne,en-US;q=0.9,en;q=0.8",
}

#: How long a local read may take.
LOCAL_TIMEOUT_SECONDS = 20.0

#: The shortest text that counts as an article.
#:
#: A page that trafilatura can pull a paragraph out of is a page with
#: a body. A page it cannot is a listing, a home page, or a 404 that
#: answers with 200 — and treating that as an article is how a listing
#: page's own headline ends up as a story's headline.
MIN_ARTICLE_CHARS = 200


@dataclass
class LocalArticle:
    """What we could read out of a page, ourselves."""

    #: The article's body, as plain text.
    text: str
    #: The headline, if the page declared one we could use.
    title: str | None = None
    #: The page's own image, if it declared one.
    image: str | None = None
    #: The page's own description.
    description: str | None = None
    #: The author, if the page declared one.
    author: str | None = None


def _text_of(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field(document: Any, name: str) -> Any:
    """
    One field of an extracted document.
+
    `trafilatura.bare_extraction` hands back a dict in some versions and
    an object in others, and both are in the wild depending on which
    one is installed. Reading the field the way the installed version
    stores it is the difference between an extractor that works and
    one that raises on every page.
    """
    if document is None:
        return None
    if isinstance(document, dict):
        return document.get(name)
    return getattr(document, name, None)


def extract_locally(
    html: str,
    max_chars: int = 20_000,
    favor_precision: bool = True,
) -> LocalArticle | None:
    """
    Read the article out of a page that is already in hand.

    None when the page holds no article — a listing, a home page, an
    error page. Trafilatura is good at telling them apart, which is the
    whole point of using it here.

    `favor_precision` asks for a short, confident extract and leaves
    out anything the extractor is unsure about — the right answer when
    the question is "is this an article?". Passing False asks for
    recall instead, which is the right answer when the question is
    "give me the body".
    """
    if not html or not html.strip():
        return None

    if trafilatura is None:
        # The library is missing, which should never happen (it is in
        # requirements.txt), but a missing library must not take the
        # pass down with it.
        return None

    try:
        document = trafilatura.bare_extraction(
            html,
            include_links=False,
            include_images=False,
            include_tables=False,
            favor_precision=favor_precision,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Local extraction failed: %s", str(exc)[:160])
        return None

    if not document:
        return None

    text = _text_of(_field(document, "text")) or ""
    if len(text) < MIN_ARTICLE_CHARS:
        return None

    image = None
    image_field = _field(document, "image")
    if image_field:
        image = _text_of(image_field)

    return LocalArticle(
        text=text[:max_chars],
        title=_text_of(_field(document, "title")),
        image=image,
        description=_text_of(_field(document, "description")),
        author=_text_of(_field(document, "author")),
    )


async def fetch_html(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = LOCAL_TIMEOUT_SECONDS,
) -> str | None:
    """
    Fetch a page, ourselves.

    None when the page is refused, gone, or unreadable. It never
    raises: a fallback that raises is not a fallback.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    try:
        response = await client.get(
            url,
            headers=BROWSER_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not fetch %s locally: %s", url[:120], str(exc)[:160])
        return None

    if response.status_code >= 400:
        logger.info(
            "%s answered %s for a local read", url[:90], response.status_code
        )
        return None

    text = response.text or ""
    return text if text.strip() else None


async def fetch_article(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = LOCAL_TIMEOUT_SECONDS,
) -> LocalArticle | None:
    """
    Fetch a page and read the article out of it, in one request.

    This is the tier that cannot expire: no key, no quota, no service
    between us and the publisher.
    """
    html = await fetch_html(url, client, timeout)
    if not html:
        return None
    return extract_locally(html)


async def page_has_article(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = LOCAL_TIMEOUT_SECONDS,
) -> bool:
    """
    Whether a URL is an article page.

    This is the question the listing verifier asks, and it used to ask
    it through a service with a quota. Locally it is one request and
    no quota at all.
    """
    html = await fetch_html(url, client, timeout)
    if not html:
        return False
    return extract_locally(html) is not None
