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
import os
import time

import httpx

from app.sources import local_reader

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
#: So the reader is asked at most once every second and a half, whatever
#: the caller is doing. The wait is here rather than in the callers
#: because the limit belongs to the reader, not to the sweep.
#:
#: It used to be three seconds, and the cost was measured: a pass
#: that had to read six hundred pages through the reader took longer
#: than the job's timeout, so it was killed half way through, and the
#: next pass started the same six hundred again. The site went an hour
#: without a new story because a backfill of the archive was bigger
#: than the window it had to fit in.
#:
#: A reader that answers "slow down" is handled below: the page is
#: skipped, the story is still stored, and the next pass reads it
#: again. A story that arrives late is better than a story that never
#: arrives.
#:
#: 3.1 seconds, which is nineteen requests a minute - the free
#: reader's actual ceiling.
#:
#: It used to be 1.5, which is forty a minute, and that is twice what
#: the free reader serves. The excess came back as "slow down", and a
#: page that is skipped is a page that is never read: no headline from
#: the story's own page, no picture proven against it. The symptom was
#: a page of cards whose headlines were still the listing card's whole
#: text, each one showing its key points where the photograph should
#: have been.
#:
#: Half the reads were being thrown away by a limit we set ourselves.
#: The cost of obeying the limit is a slower pass; the cost of
#: ignoring it is that the pictures never arrive.
READER_MIN_INTERVAL_SECONDS = 3.1

#: With an API key the reader serves sixty pages a minute instead of
#: twenty, and the wait between reads is a third of what it was. The
#: key is the difference between a pass that reads every publisher's
#: article pages and a pass that gives up on the ones that refuse
#: this address.
READER_MIN_INTERVAL_SECONDS_WITH_KEY = 1.0

_reader_lock: asyncio.Lock | None = None
_reader_lock_loop: object = None
_reader_last_call: float = 0.0


def _reader_lock_for_this_loop() -> asyncio.Lock:
    """
    The lock that holds the reader to its own pace, for THIS loop.

    An asyncio.Lock binds itself to the loop that contends for it, and
    raises "bound to a different event loop" when it is used from
    another one. The worker serves its HTTP API from a second loop in
    a second thread, and the tests each run on their own loop, so a
    lock bound once and kept for ever is a crash waiting for the first
    time two of them are alive at once.

    So the lock is rebuilt when the loop changes. There is still only
    ONE lock at a time, and the pace is still held globally by
    `_reader_last_call`, which is a plain number and belongs to no
    loop.
    """
    global _reader_lock, _reader_lock_loop

    loop = asyncio.get_running_loop()

    if _reader_lock is None or _reader_lock_loop is not loop:
        _reader_lock = asyncio.Lock()
        _reader_lock_loop = loop

    return _reader_lock


def _api_key() -> str:
    """
    The reader's API key, if one is configured.

    Read from the settings, and from the environment directly, because
    the two live in different places: the settings file is how the
    deployment configures it, and the environment is what a CI run
    and a developer machine both have.
    """
    for value in (
        os.environ.get("JINA_API_KEY"),
        os.environ.get("Jina_API_Key"),
    ):
        if value and value.strip():
            return value.strip()

    try:
        from app.config import get_settings

        return (get_settings().jina_api_key or "").strip()
    except Exception:
        return ""


async def _wait_for_the_reader() -> None:
    """Hold the reader to its own pace."""
    global _reader_last_call
    interval = (
        READER_MIN_INTERVAL_SECONDS_WITH_KEY
        if _api_key()
        else READER_MIN_INTERVAL_SECONDS
    )
    async with _reader_lock_for_this_loop():
        elapsed = time.monotonic() - _reader_last_call
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
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

    # THE KEY, WHEN THERE IS ONE
    # --------------------------
    # The reader serves twenty pages a minute to an address with no
    # key and sixty to one with a key, and the key is a CREDENTIAL: it
    # is read from the environment, it is sent only to the reader, and
    # it is never logged.
    #
    # The key also shortens the wait between reads, which is what
    # lets a pass read every publisher's article pages instead of
    # giving up on the ones that refuse this address.
    api_key = _api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
        # 429 is the reader saying "slow down". The page is skipped and
        # the story is stored without it - a late story beats no
        # story - and the next pass reads it again.
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


#: Where a page came from. The three tiers, in the order they are tried.
SOURCE_PUBLISHER = "publisher"
SOURCE_READER = "reader"
SOURCE_LOCAL = "local"
SOURCE_REFUSED = "refused"
SOURCE_UNREADABLE = "unreadable"


async def fetch_with_local_fallback(
    url: str,
    client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    proxy: str = DEFAULT_READER_PROXY,
    timeout: float = PROXY_TIMEOUT_SECONDS,
) -> tuple[str | None, str]:
    """
    Read a page through every tier there is, and say which one read it.

    THE THREE TIERS
    ---------------
        1. the publisher itself, from this address;
        2. the reader proxy, from an address the publisher has not
           refused;
        3. us, here, on this machine - httpx and trafilatura, no key,
           no quota and no service in the middle.

    The third tier is a FALLBACK and never a replacement. The proxy is
    still asked first for every page, because the proxy is the only
    tier that can reach a publisher that refuses this address.

    What the local tier adds is everything the proxy was being used
    for that is not really about a refusal:

      - PROVING that a page is an article, for one request and no
        quota at all. The listing verifier used to spend a
        quota-bound read to answer that question;
      - reading a page the proxy was rate-limited on, which is the
        whole reason a reader with a free tier is a liability.

    Returns the page's text and where it came from: "publisher",
    "reader" or "local". When no tier could read it, the text is None
    and the source is "refused" (the publisher answered that the page
    is not there) or "unreadable" (nothing answered at all).

    It never raises: a fallback that raises is not a fallback.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None, SOURCE_UNREADABLE

    request_headers = (
        dict(headers) if headers else dict(local_reader.BROWSER_HEADERS)
    )

    response = None
    try:
        response = await client.get(
            url, headers=request_headers, follow_redirects=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not fetch %s: %s", url[:120], exc)

    if response is not None:
        if response.status_code < 400:
            return response.text, SOURCE_PUBLISHER
        if response.status_code not in BLOCKED_STATUS_CODES:
            # A real answer about the page: it is gone. Reading it
            # somewhere else would only find the same 404.
            return None, SOURCE_REFUSED

    # The publisher refused this address, or could not be reached.
    text = await fetch_via_reader(url, client, proxy=proxy, timeout=timeout)
    if text:
        return text, SOURCE_READER

    # The reader could not fetch it either. Read it ourselves. This
    # tier cannot reach a publisher that refuses us, but it CAN reach
    # a page the proxy was too busy to fetch, and it costs no quota.
    text = await local_reader.fetch_html(url, client)
    if text:
        logger.info("Read %s ourselves, with no reader in the middle.", url[:120])
        return text, SOURCE_LOCAL

    return None, SOURCE_UNREADABLE
