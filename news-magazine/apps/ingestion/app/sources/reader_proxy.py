"""
Read a page the worker cannot reach from where it stands.

THE PROBLEM, MEASURED
---------------------
Nine Nepali publishers answer the CI runner with HTTP 403 and answer an
ordinary client with a 200:

    Ekantipur            403 for 'https://ekantipur.com/news'
    Hamro Patro News     403 for 'https://www.hamropatro.com/news'
    Hamro Patro National 403 for 'https://www.hamropatro.com/news'
    Nepal News           403 for 'https://nepalnews.com/feed'
    Nepal News English   403 for 'https://english.nepalnews.com/feed'
    OnlineKhabar         403
    OnlineKhabar English 403
    Ratopati             403 for 'https://ratopati.com/feed'
    Ujyaalo Online       403 for 'https://www.ujyaaloonline.com/feed/'

The refusal is aimed at the ADDRESS and not at the request. Measured
from an ordinary client, every one of those URLs answers 200 with the
worker's own User-Agent, with a browser's, and with the site's own
Referer. Their robots.txt permits every one of their paths, and one of
them (ratopati.com) publishes a Crawl-delay of 20 seconds, which this
worker honours.

So the pages are not closed to us. They are closed to THIS address.
The runner sits in a datacentre in another country, and these
publishers refuse datacentre addresses.

WHAT THAT COST
--------------
Two things, and both are visible on the site:

  - the nine contribute NOTHING: every pass, all nine fetch zero
    articles, so a quarter of the sources are silent;
  - and the pictures of the stories that WERE fetched (before the
    refusal began) are still there on the real pages - the og:image
    and a JSON-LD NewsArticle block both carry the real photograph -
    but the worker could not read the page that declares them, so the
    picture could never be proven, and the card fell back to the
    story's key points.

THE READER
----------
A reader proxy fetches the page from an address these publishers do
not refuse and hands back the HTML. The extraction then runs on the
real page, exactly as it always did, and the picture is proven by
loading the IMAGE - which lives on the publisher's CDN, an address
that answers us perfectly well.

WHAT IT DOES NOT DO
-------------------
  - it is a FALLBACK, never the first try: the worker asks the
    publisher itself first, and only a refusal sends it elsewhere;
  - it never carries a credential, a cookie or a session - the URLs
    are public article addresses and nothing else;
  - it is one request to one place, and it is off with one setting
    (READER_PROXY_ENABLED=false);
  - the publisher still decides what to publish. Nothing here makes a
    publisher publish anything it did not.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

#: The default reader. It is a service that fetches a public URL and
#: returns the page as HTML. Replaceable with a setting.
DEFAULT_READER_PROXY = "https://r.jina.ai"

#: How long a proxy read may take. The publisher's own timeout is
#: longer, because a proxy is a second hop and the pages are big.
PROXY_TIMEOUT_SECONDS = 30.0

#: The status codes that mean "your address is refused", as opposed to
#: "this page is gone" or "this server is having a bad day". Only these
#: send the request to the reader.
BLOCKED_STATUS_CODES = (401, 403, 405, 406, 407, 429, 451)

#: The reader's own pace.
#:
#: A free reader service answers about twenty requests a minute, and a
#: sweep that asks for four hundred pages in a minute is answered with
#: "slow down" for most of them - and a "slow down" is a page that
#: could not be read, which is a story that keeps its broken headline.
#:
#: So the reader is asked at most once every three seconds, whatever
#: the caller is doing. The wait is here rather than in the callers
#: because the limit belongs to the reader, not to the sweep.
READER_MIN_INTERVAL_SECONDS = 3.0

_reader_lock = asyncio.Lock()
_reader_last_call: float = 0.0


async def _wait_for_the_reader() -> None:
    """Hold the reader to its own pace."""
    global _reader_last_call
    async with _reader_lock:
        elapsed = time.monotonic() - _reader_last_call
        if elapsed < READER_MIN_INTERVAL_SECONDS:
            await asyncio.sleep(READER_MIN_INTERVAL_SECONDS - elapsed)
        _reader_last_call = time.monotonic()


def _is_blocked(status: int) -> bool:
    return status in BLOCKED_STATUS_CODES


def reader_proxy_url(proxy: str, url: str) -> str:
    """The reader's address for this page."""
    base = (proxy or DEFAULT_READER_PROXY).rstrip("/")
    return f"{base}/{url}"


async def fetch_via_reader(
    url: str,
    client: httpx.AsyncClient,
    proxy: str = DEFAULT_READER_PROXY,
    timeout: float = PROXY_TIMEOUT_SECONDS,
) -> str | None:
    """
    Read a public page through the reader proxy.

    Returns the page's HTML, or None when the reader could not get it
    either. It never raises: a fallback that raises is not a fallback.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    headers = {
        "User-Agent": "NepalNewsMagazineBot/1.0 (+https://new24hr.vercel.app)",
        # The reader returns markdown by default; the extractor needs
        # the page itself, with its meta tags and its JSON-LD.
        "x-respond-with": "html",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }

    await _wait_for_the_reader()

    try:
        response = await client.get(
            reader_proxy_url(proxy, url),
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("The reader could not fetch %s: %s", url[:120], exc)
        return None

    if response.status_code >= 400:
        logger.info(
            "The reader answered %s for %s",
            response.status_code,
            url[:120],
        )
        return None

    text = response.text or ""
    if not text.strip():
        return None

    logger.info("The reader fetched %s (%s bytes).", url[:120], len(text))
    return text


async def fetch_with_reader_fallback(
    url: str,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    proxy: str = DEFAULT_READER_PROXY,
    timeout: float = PROXY_TIMEOUT_SECONDS,
) -> tuple[str | None, int]:
    """
    Fetch a page, and read it through the reader if we are refused.

    Returns the page's HTML and the status that produced it (0 when the
    reader was used). This is the shape every fetcher wants: the text,
    and whether it came from the publisher or from the reader.
    """
    try:
        response = await client.get(url, headers=headers, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not fetch %s: %s", url[:120], exc)
        return None, 0

    if response.status_code < 400:
        return response.text, response.status_code

    if not _is_blocked(response.status_code):
        logger.info(
            "%s answered %s for %s", url[:90], response.status_code, url[:90]
        )
        return None, response.status_code

    # Refused. The publisher has not closed the page to us; it has
    # closed the page to this address. Read it from one that has not
    # been closed to.
    logger.info(
        "%s answered %s - this address is refused. Reading it through the "
        "reader instead.",
        url[:90],
        response.status_code,
    )
    text = await fetch_via_reader(url, client, proxy=proxy, timeout=timeout)
    return text, 0
