"""
Generic RSS/Atom adapter.

This adapter supports permitted RSS/Atom feeds only.

It does NOT scrape article pages or republish full article content.
Only information explicitly provided by the RSS/Atom feed is normalized:

    - headline
    - source URL
    - published/updated timestamp
    - author
    - summary/excerpt
    - thumbnail/image URL
    - optional media/video URL when explicitly provided by the feed

An administrator must verify that a source/feed is permitted before
setting allowed=true.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from app.sources.base import NormalizedArticle, SourceAdapter
from app.sources import local_reader
from app.sources.reader_proxy import (
    BLOCKED_STATUS_CODES,
    fetch_via_reader,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WORDPRESS HOSTS
#
# A WordPress feed serves a fixed number of items: the site's
# "posts_per_rss" option, which is 10 for most WordPress sites and 5
# for several Nepali ones. The operator wants more than that.
#
# Only these hosts get a page-size parameter or a second page
# appended. A feed that is not WordPress is fetched exactly as it was
# before, because neither parameter means anything to a non-WordPress
# server and both change a URL that does not need changing.
#
# Each host below was MEASURED, not guessed: the feed was fetched and
# its <generator> tag read. A generator of
# "https://wordpress.org/?v=X.Y.Z" is WordPress. Where the generator
# is absent but the feed body carries "wp-content" (the asset path
# every WordPress theme uses), it is WordPress too. Everything else on
# the list of Nepali outlets that was checked -- ratopati.com,
# setopati.com, nagariknews.com, nepalitimes.com, nepalsamaya.com,
# osnepal.com, ujyaaloonline.com, himalkhabar.com, thahakhabar.com,
# baahrakhari.com -- serves a feed with no WordPress marker at all.
#
# The operator can extend this list without a deploy by setting
# RSS_WORDPRESS_HOSTS (comma-separated).
# ---------------------------------------------------------------------------

KNOWN_WORDPRESS_HOSTS = frozenset({
    # Already seeded.
    "www.onlinekhabar.com",
    "english.onlinekhabar.com",
    "nepalnews.com",
    "english.nepalnews.com",
    "nagariktimes.com",
    "www.khabarzone.com",
    "www.pradeshpost.com",
    # Verified in the 2026-10-22 outlet round.
    "www.karobardaily.com",
    "newsofnepal.com",
    "www.kathmandutribune.com",
    "techmandu.com",
    "rajdhanidaily.com",
    "arthasarokar.com",
    "www.nepalipatra.com",
    "www.telegraphnepal.com",
    "onlinetvnepal.com",
    "bizkhabar.com",
})


#: Never ask for more pages than this, whatever the setting says.
#: Every extra page is one extra request to a publisher.
MAX_FEED_PAGES = 10


def _wordpress_hosts(settings) -> set[str]:
    """
    The hosts whose feeds may be paged.

    The built-in list, plus whatever the operator added through
    RSS_WORDPRESS_HOSTS.
    """
    hosts = set(KNOWN_WORDPRESS_HOSTS)

    extra = getattr(settings, "rss_wordpress_hosts", None)

    if extra:
        for host in str(extra).split(","):
            host = host.strip().lower()
            if host:
                hosts.add(host)

    return hosts


def build_feed_urls(feed_url: str, settings) -> list[str]:
    """
    The URLs to fetch for one poll of one feed.

    Returns a list of one URL in the ordinary case, and more than one
    only when the host is a known WordPress install and the operator
    has asked for more than a single page.

    Two parameters can be added, and only to a WordPress host:

        posts_per_page=<n>   the page size asked for
        paged=<k>            the k-th page of the feed

    MEASURED 2026-10-22 on the live Nepali feeds, which is why both
    exist and why the second one is the one that matters:

        ?posts_per_page=100   changes NOTHING. The item count and the
                              first item are identical with and without
                              it on every WordPress feed tested
                              (10 of 10). WordPress pins the main
                              feed's posts_per_page to the
                              posts_per_rss option and ignores the
                              query string. It is still sent, because
                              it is free and it does help any site
                              that does honour it.

        ?paged=2, ?paged=3    DO work. They return the NEXT page of
                              items -- older stories, the same count
                              per page. This is the only mechanism
                              measured that actually gets more items
                              out of a WordPress feed.

    A URL that already has a query string of its own is extended with
    "&", not "?", so nothing is thrown away.
    """
    page_size = int(getattr(settings, "rss_page_size", 0) or 0)
    param = (
        getattr(settings, "rss_page_size_param", "posts_per_page")
        or "posts_per_page"
    ).strip()
    pages = int(getattr(settings, "rss_feed_pages", 1) or 1)

    pages = max(1, min(pages, MAX_FEED_PAGES))

    # Nothing to add: fetch the feed exactly as it is configured.
    if page_size <= 0 and pages <= 1:
        return [feed_url]

    host = (urlparse(feed_url).hostname or "").lower()

    if host not in _wordpress_hosts(settings):
        return [feed_url]

    def extend(url: str, extra: str) -> str:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{extra}"

    base = feed_url

    if page_size > 0 and param:
        base = extend(base, f"{param}={page_size}")

    urls = []

    for page in range(1, pages + 1):
        if page == 1:
            urls.append(base)
        else:
            urls.append(extend(base, f"paged={page}"))

    return urls


# ---------------------------------------------------------------------------
# XML REPAIR
#
# A FEED IS NOT ALWAYS WELL-FORMED
# -------------------------------
# One Nepali publisher serves a feed that is not valid XML, and the
# error it produced was this, every two minutes, all day:
#
#     Unable to parse RSS/Atom feed: <unknown>:9:14: not well-formed
#     (invalid token)
#
# That is expat's way of saying "there is a byte in this document that
# XML does not allow". The usual culprits are a raw control character
# (a vertical tab or a form feed pasted out of a Word document into
# the editor), a byte-order mark in the middle of the document, and a
# bare `&` where an entity was meant.
#
# The document is not garbage. It carries the outlet's headlines, and
# the headlines are the whole point of the pass. So the body is
# REPAIRED before it is parsed, and the error is reported only if the
# repaired body still fails - which is the difference between one
# broken feed and one silent publisher.
# ---------------------------------------------------------------------------

#: The characters XML 1.0 does not allow in a document.
#:
#: Everything below 0x20 except tab (0x09), newline (0x0A) and
#: carriage return (0x0D), plus 0x7F (DEL). A vertical tab (0x0B) or
#: a form feed (0x0C) pasted out of a word processor into a headline
#: is the classic one, and it is invisible in every editor.
_XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: The byte-order mark, as a character.
_BOM = "﻿"

#: A `&` that does not begin a valid XML entity reference.
#:
#: XML knows five named entities (amp, lt, gt, quot, apos) and numeric
#: character references. Everything else - `&nbsp;` included - is not
#: a valid reference, and expat rejects it.
_BARE_AMPERSAND_RE = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#[xX][0-9a-fA-F]+);)"
)


def sanitize_xml_text(text: str) -> str:
    """
    Remove the bytes XML does not allow from a document.

    Three things are removed, and only these three:

        - a byte-order mark, wherever it is (one at the start is
          legal, one in the middle is not, and one at the start of a
          document that was decoded as UTF-8 is a byte the parser
          may not expect);
        - every control character below 0x20 except tab, newline and
          carriage return;
        - 0x7F, DEL, which is legal in XML 1.1 and not in XML 1.0.

    Nothing else is touched. The text, the entities and the markup
    are left exactly as the publisher wrote them.
    """
    if not text:
        return text

    cleaned = text.replace(_BOM, "")
    return _XML_ILLEGAL_RE.sub("", cleaned)


def _escape_bare_ampersands(text: str) -> str:
    """
    Escape the `&` that do not begin a valid XML entity reference.

    The last resort, and only used when everything else has failed:
    it changes the document's meaning slightly (`&nbsp;` becomes the
    four characters `&amp;nbsp;`), which is why it is tried last.
    """
    return _BARE_AMPERSAND_RE.sub("&amp;", text)


def _recover_with_lxml(text: str) -> str | None:
    """
    Rebuild a malformed document with lxml's recovering parser.

    lxml is in requirements.txt, but the import is guarded anyway:
    a feed must never be the reason the worker stops, and a missing
    optional parser must not become one.

    Returns the rebuilt document, or None when lxml could not help.
    """
    try:
        from lxml import etree
    except Exception:  # pragma: no cover - lxml is in requirements.txt
        return None

    try:
        parser = etree.XMLParser(
            recover=True,
            resolve_entities=False,
            no_network=True,
            huge_tree=False,
        )
        root = etree.fromstring(text.encode("utf-8"), parser)
    except Exception:
        return None

    if root is None:
        return None

    try:
        return etree.tostring(root, encoding="unicode")
    except Exception:
        return None


def _repair_candidates(document: str) -> list[str]:
    """
    The repaired forms of a malformed document, cheapest first.

    Each one is a document that MIGHT parse. They are tried in order
    and the first that yields entries wins.
    """
    candidates: list[str] = []

    sanitised = sanitize_xml_text(document)
    if sanitised != document:
        candidates.append(sanitised)

    # The sanitised document may still be malformed, and the commonest
    # remaining fault is a bare `&`.
    escaped = _escape_bare_ampersands(sanitised)
    if escaped != sanitised:
        candidates.append(escaped)

    recovered = _recover_with_lxml(sanitised)
    if recovered and recovered != sanitised:
        candidates.append(recovered)

    return candidates


def _parse_feed_document(document: str) -> tuple[object, bool, object | None]:
    """
    Parse one feed document, repairing it first if it is malformed.

    Returns three things:

        the parsed feed;
        whether the document had to be repaired;
        the fault the ORIGINAL document had, if any.

    The third one is why the second exists. A document that is not
    well-formed XML is repaired, and a repaired document that is not a
    feed is still not a feed - the error the operator sees must be the
    error the PUBLISHER's document had, not the error a document we
    rewrote happens to have.

    A document that parses as it stands is parsed as it stands: the
    repair is never paid for unless it is needed.
    """
    parsed = feedparser.parse(document)

    if not getattr(parsed, "bozo", False):
        return parsed, False, None

    original_error = getattr(parsed, "bozo_exception", None)

    best = parsed
    repaired = False

    for candidate in _repair_candidates(document):
        attempt = feedparser.parse(candidate)
        clean = not getattr(attempt, "bozo", False)

        # A repair is only taken when it is at least as good as what
        # we already had: a document that parses cleanly, or one that
        # holds MORE entries. feedparser's forgiving parser can
        # recover entries the strict one would not, so a "clean" but
        # emptier document is not an improvement.
        if clean and len(attempt.entries) >= len(best.entries):
            best = attempt
            repaired = True
            break
        if len(attempt.entries) > len(best.entries):
            best = attempt
            repaired = True

    if repaired:
        logger.info(
            "Repaired a malformed feed: %s bytes -> %s entries.",
            len(document),
            len(best.entries),
        )

    return best, repaired, original_error


# ---------------------------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """
    Normalize a title for duplicate detection.
    """
    return re.sub(r"\s+", " ", title or "").strip().lower()


def _content_hash(
    canonical_url: str,
    title_normalized: str,
) -> str:
    """
    Generate a stable SHA-256 content hash.
    """
    value = f"{canonical_url}|{title_normalized}"

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _strip_html(value: str | None) -> str | None:
    """
    Convert feed HTML into safe plain text.

    RSS summaries frequently contain:
        <p>...</p>
        <img ...>
        &nbsp;
        HTML entities
    """
    if not value:
        return None

    value = html.unescape(value)

    # Remove script/style blocks first.
    value = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove all remaining HTML tags.
    value = re.sub(
        r"<[^>]+>",
        " ",
        value,
    )

    # Normalize whitespace.
    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return value or None


def _clean_url(
    value: str | None,
    base_url: str | None = None,
) -> str | None:
    """
    Normalize a URL.

    Handles:
        absolute URLs
        protocol-relative URLs
        relative URLs

    Rejects non-http(s) URLs.
    """
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    # Convert protocol-relative URLs.
    if value.startswith("//"):
        value = "https:" + value

    # Resolve relative URLs against feed URL.
    if base_url:
        value = urljoin(
            base_url,
            value,
        )

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        return None

    if not parsed.netloc:
        return None

    return value


def _get_entry_url(
    entry: dict,
    feed_url: str,
) -> str | None:
    """
    Extract the canonical article URL.

    Priority:
        entry.link
        Atom/RSS link objects
    """
    link = entry.get("link")

    if link:
        return _clean_url(
            link,
            feed_url,
        )

    links = entry.get("links") or []

    if isinstance(links, list):
        # Prefer alternate article links.
        for item in links:
            if not isinstance(item, dict):
                continue

            href = item.get("href")
            rel = item.get("rel")

            if href and rel in {None, "alternate"}:
                cleaned = _clean_url(
                    href,
                    feed_url,
                )

                if cleaned:
                    return cleaned

    return None


# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------

def _parse_struct_time(value) -> str | None:
    """
    Convert feedparser's parsed time tuple into UTC ISO format.
    """
    if not value:
        return None

    try:
        return datetime(
            *value[:6],
            tzinfo=timezone.utc,
        ).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _get_published_date(entry: dict) -> str | None:
    """
    RSS/Atom feeds use different date fields.

    Try:
        published_parsed
        updated_parsed
        created_parsed
    """
    for field in (
        "published_parsed",
        "updated_parsed",
        "created_parsed",
    ):
        parsed = entry.get(field)

        result = _parse_struct_time(parsed)

        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# IMAGE HELPERS
# ---------------------------------------------------------------------------

def _extract_url_from_value(
    value,
    feed_url: str,
) -> str | None:
    """
    Extract a URL from the many shapes returned by feedparser.
    """
    if not value:
        return None

    # Simple string.
    if isinstance(value, str):
        return _clean_url(
            value,
            feed_url,
        )

    # Dictionary:
    # {"url": "..."}
    # {"href": "..."}
    # {"src": "..."}
    if isinstance(value, dict):
        for key in (
            "url",
            "href",
            "src",
        ):
            candidate = value.get(key)

            cleaned = _clean_url(
                candidate,
                feed_url,
            )

            if cleaned:
                return cleaned

    return None


def _extract_image_from_html(
    value: str | None,
    feed_url: str,
) -> str | None:
    """
    Extract the first image URL from RSS HTML content.

    Example:
        <img src="https://example.com/image.jpg">
    """
    if not value:
        return None

    # src="..."
    match = re.search(
        r"""<img[^>]+src=["']([^"']+)["']""",
        value,
        flags=re.IGNORECASE,
    )

    if match:
        return _clean_url(
            html.unescape(match.group(1)),
            feed_url,
        )

    # data-src="..." used by some feeds.
    match = re.search(
        r"""<img[^>]+data-src=["']([^"']+)["']""",
        value,
        flags=re.IGNORECASE,
    )

    if match:
        return _clean_url(
            html.unescape(match.group(1)),
            feed_url,
        )

    return None


def _extract_thumbnail(
    entry: dict,
    feed_url: str,
) -> str | None:
    """
    Extract an article thumbnail from common RSS/Atom formats.

    Priority:

        1. media_content
        2. media_thumbnail
        3. enclosures
        4. RSS/Atom image fields
        5. HTML <img> inside summary/content
    """

    # ---------------------------------------------------------
    # 1. media_content
    # ---------------------------------------------------------
    media_content = entry.get("media_content")

    if media_content:
        if isinstance(
            media_content,
            dict,
        ):
            media_content = [media_content]

        if isinstance(
            media_content,
            list,
        ):
            # Prefer actual image media.
            for media in media_content:
                if not isinstance(media, dict):
                    continue

                media_type = (
                    media.get("type")
                    or ""
                ).lower()

                if media_type.startswith("image/"):
                    result = _extract_url_from_value(
                        media,
                        feed_url,
                    )

                    if result:
                        return result

            # Fallback to first media URL.
            for media in media_content:
                result = _extract_url_from_value(
                    media,
                    feed_url,
                )

                if result:
                    return result

    # ---------------------------------------------------------
    # 2. media_thumbnail
    # ---------------------------------------------------------
    media_thumbnail = entry.get(
        "media_thumbnail"
    )

    if media_thumbnail:
        if isinstance(
            media_thumbnail,
            dict,
        ):
            media_thumbnail = [media_thumbnail]

        if isinstance(
            media_thumbnail,
            list,
        ):
            for media in media_thumbnail:
                result = _extract_url_from_value(
                    media,
                    feed_url,
                )

                if result:
                    return result

    # ---------------------------------------------------------
    # 3. RSS enclosure
    # ---------------------------------------------------------
    enclosures = entry.get("enclosures") or []

    if isinstance(
        enclosures,
        list,
    ):
        for enclosure in enclosures:
            if not isinstance(
                enclosure,
                dict,
            ):
                continue

            media_type = (
                enclosure.get("type")
                or ""
            ).lower()

            href = (
                enclosure.get("href")
                or enclosure.get("url")
            )

            # Prefer image enclosures.
            if (
                media_type.startswith("image/")
                and href
            ):
                result = _clean_url(
                    href,
                    feed_url,
                )

                if result:
                    return result

    # ---------------------------------------------------------
    # 4. entry.image
    # ---------------------------------------------------------
    image = entry.get("image")

    result = _extract_url_from_value(
        image,
        feed_url,
    )

    if result:
        return result

    # ---------------------------------------------------------
    # 5. entry.thumbnail
    # ---------------------------------------------------------
    thumbnail = entry.get("thumbnail")

    result = _extract_url_from_value(
        thumbnail,
        feed_url,
    )

    if result:
        return result

    # ---------------------------------------------------------
    # 6. Search summary/content HTML
    # ---------------------------------------------------------
    for field in (
        "summary",
        "description",
        "content",
    ):
        value = entry.get(field)

        if isinstance(
            value,
            list,
        ):
            for item in value:
                if isinstance(
                    item,
                    dict,
                ):
                    value = item.get(
                        "value"
                    )

                    result = _extract_image_from_html(
                        value,
                        feed_url,
                    )

                    if result:
                        return result

        elif isinstance(
            value,
            str,
        ):
            result = _extract_image_from_html(
                value,
                feed_url,
            )

            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# VIDEO HELPERS
# ---------------------------------------------------------------------------

def _extract_video_url(
    entry: dict,
    feed_url: str,
) -> str | None:
    """
    Extract a video URL only when the feed explicitly provides one.

    This does not scrape article pages.
    """

    media_content = entry.get(
        "media_content"
    )

    if media_content:
        if isinstance(
            media_content,
            dict,
        ):
            media_content = [media_content]

        if isinstance(
            media_content,
            list,
        ):
            for media in media_content:
                if not isinstance(
                    media,
                    dict,
                ):
                    continue

                media_type = (
                    media.get("type")
                    or ""
                ).lower()

                if (
                    media_type.startswith("video/")
                    or media_type in {
                        "application/x-shockwave-flash",
                    }
                ):
                    result = _extract_url_from_value(
                        media,
                        feed_url,
                    )

                    if result:
                        return result

    # Look at enclosures for video.
    enclosures = entry.get(
        "enclosures"
    ) or []

    if isinstance(
        enclosures,
        list,
    ):
        for enclosure in enclosures:
            if not isinstance(
                enclosure,
                dict,
            ):
                continue

            media_type = (
                enclosure.get("type")
                or ""
            ).lower()

            if media_type.startswith(
                "video/"
            ):
                result = _clean_url(
                    enclosure.get(
                        "href"
                    )
                    or enclosure.get(
                        "url"
                    ),
                    feed_url,
                )

                if result:
                    return result

    return None


# ---------------------------------------------------------------------------
# RSS ADAPTER
# ---------------------------------------------------------------------------

class GenericRssAdapter(SourceAdapter):

    def __init__(self, source_row: dict):
        super().__init__(source_row)

        # The raw documents fetched for this poll. There is normally
        # exactly one; there is more than one only when the operator
        # asked for extra pages of a WordPress feed, in which case
        # parse() merges them.
        self._documents: list[str] = []

    def _outlet_referer(self) -> str | None:
        """
        The outlet's own homepage, to be sent as the Referer.

        This is where a reader of the outlet's site would have come
        from. It says nothing we are not: it is the publisher's own
        address, on a path its robots.txt allows for everyone.
        """
        for field in ("site_url", "base_url"):
            value = self.source.get(field)
            if value:
                return str(value)
        return None

    async def fetch(self) -> str:
        """
        Fetch the configured RSS/Atom feed.

        Only fetches the feed itself.
        Does not crawl article pages.

        For a known WordPress host, the operator may have asked for
        more than the first page of the feed. Each extra page is one
        extra request, and the rate limiter is waited on between
        them, so a paged fetch is still inside the rate this source
        declared it tolerates.

        The limiter it waits on is the publisher's own budget: one
        per host, shared by every poll of that host for the life of the
        process, and never faster than the Crawl-delay the publisher
        states in its robots.txt. See app/sources/base.py.
        """

        feed_url = self.source.get(
            "feed_url"
        )

        if not feed_url:
            raise ValueError(
                f"Source '{self.source.get('name', 'unknown')}' "
                "does not have a feed_url."
            )

        if not await self.check_allowed(
            feed_url
        ):
            raise PermissionError(
                f"Source '{self.source.get('name', 'unknown')}' "
                f"is not marked allowed=true and "
                f"robots.txt-verified; refusing to fetch "
                f"{feed_url}."
            )

        headers = {
            "User-Agent": self.settings.default_user_agent,
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml, "
                "*/*;q=0.8"
            ),
            # A normal reader sends a language and a referer; a bare
            # request with neither is what a WAF's "is this a browser?"
            # heuristics look at first. Both are free and neither
            # pretends to be anything we are not: the language is a
            # preference, and the referer is the OUTLET'S OWN HOMEPAGE,
            # on a path its robots.txt allows for everyone.
            #
            # Measured 2026-10-23 on the five Nepali publishers that
            # were refusing us with HTTP 403 (nepalnews.com,
            # english.nepalnews.com, ratopati.com, ujyaaloonline.com,
            # ekantipur.com): from an ordinary client every one of
            # them answers 200 with the honest User-Agent, with a
            # browser User-Agent, with a Referer and with
            # Accept-Language. The refusal was never about these
            # headers - Ekantipur is refused even by the request that
            # already carries a full Chrome User-Agent - so they are
            # here to be a well-formed client, not to slip past a
            # block.
            "Accept-Language": getattr(
                self.settings,
                "default_accept_language",
                "ne,en;q=0.8",
            ),
        }

        referer = self._outlet_referer()

        if referer:
            headers["Referer"] = referer

        timeout = (
            self.settings
            .default_request_timeout_seconds
        )

        urls = build_feed_urls(
            feed_url,
            self.settings,
        )

        documents: list[str] = []

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        ) as client:

            for url in urls:
                # robots.txt is checked for EVERY url, not just the
                # first one: a paged request is a different URL and a
                # host is free to allow one and refuse the other.
                if not await self.check_allowed(url):
                    raise PermissionError(
                        f"Source '{self.source.get('name', 'unknown')}' "
                        f"is not allowed for {url}. Check sources.allowed "
                        "and the outlet's robots.txt."
                    )

                await self.rate_limiter.wait()

                response = await client.get(
                    url,
                    headers=headers,
                )

                if response.status_code >= 400:
                    # THE PUBLISHER REFUSED THIS ADDRESS
                    #
                    # Nine Nepali publishers answer the CI runner with
                    # a 403 and answer an ordinary client with a 200:
                    # the refusal is aimed at the ADDRESS, not at the
                    # request. One of them publishes its feed at
                    #
                    #     https://www.khabarzone.com/feed?posts_per_page=100
                    #
                    # and the refusal lost the outlet entirely, every
                    # pass, with the log saying so:
                    #
                    #     Client error '403 Forbidden' for url
                    #     'https://www.khabarzone.com/feed?posts_per_page=100'
                    #
                    # A refusal is not an answer about the feed. So the
                    # feed is read from an address that has not been
                    # refused - see app/sources/reader_proxy.py - and
                    # the query string is dropped first, because a
                    # publisher that refuses one URL of a path is not
                    # refusing the path.
                    text = await self._read_refused_feed(
                        url, response.status_code, client, headers
                    )

                    if text is None:
                        # Nothing worked. The publisher's own answer is
                        # the one that is reported, because it is the
                        # one that is true.
                        response.raise_for_status()
                        raise ValueError(
                            f"RSS feed could not be read from "
                            f"{url} (status {response.status_code}) "
                            f"and could not be read through the reader."
                        )

                    documents.append(text)
                    continue

                # Make sure we actually received content.
                if not response.text.strip():
                    raise ValueError(
                        f"RSS feed returned an empty response: "
                        f"{url}"
                    )

                documents.append(response.text)

            # A FEED THAT WAS FETCHED AND CANNOT BE READ
            # ------------------------------------------
            # The publisher answered 200 and the body is not a feed:
            # an HTML error page, a challenge, a truncated document.
            #
            # The repair in `_parse_feed_document` fixes a MALFORMED
            # feed - a stray control character, a bare ampersand. It
            # cannot fix a body that is not a feed at all, and a body
            # that is not a feed is what this publisher serves the
            # runner: the same URL answers a browser with the feed
            # and the runner with a page that is not one.
            #
            # The reader proxy fetches from an address the publisher
            # does not refuse, and it is the only tier that can see a
            # clean copy of a feed whose body is a block page. So the
            # feed is asked for once more, from there, and the copy
            # that parses is the one that is used.
            #
            # It is one request, and only for a feed that has already
            # failed to parse — which is a feed that is worth one.
            if documents and not self._any_readable(documents):
                recovered = await self._read_unreadable_feed(
                    urls, client, headers
                )
                if recovered:
                    documents = [recovered]

        self._documents = documents

        return documents[0]

    def _any_readable(self, documents: list[str]) -> bool:
        """Whether any of these documents holds at least one entry."""
        for document in documents:
            try:
                parsed, _repaired, _error = _parse_feed_document(document)
            except Exception:  # noqa: BLE001
                continue
            if getattr(parsed, "entries", None):
                return True
        return False

    async def _read_unreadable_feed(
        self,
        urls: list[str],
        client: httpx.AsyncClient,
        headers: dict,
    ) -> str | None:
        """
        Read a feed that was fetched and could not be read.

        The body the publisher served this address is not a feed. The
        reader proxy fetches the same URL from an address the publisher
        does not refuse, and its copy usually is one.

        Returns the body that parses, or None. It never raises: a
        fallback that raises is not a fallback.
        """
        if not getattr(self.settings, "reader_proxy_enabled", True):
            return None

        for url in urls:
            try:
                text = await fetch_via_reader(
                    url,
                    client,
                    proxy=self.settings.reader_proxy_url,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "Could not read %s through the reader: %s",
                    url[:120],
                    str(exc)[:160],
                )
                continue

            if not text or not text.strip():
                continue

            try:
                parsed, _repaired, _error = _parse_feed_document(text)
            except Exception:  # noqa: BLE001
                continue

            if getattr(parsed, "entries", None):
                logger.info(
                    "%s served a body that is not a feed; the feed was "
                    "read through the reader instead.",
                    self.source.get("name"),
                )
                return text

        return None

    def _bare_feed_url(self, url: str) -> str:
        """
        The same feed without its query string.

        `?posts_per_page=100` is the parameter this codebase asks a
        WordPress feed for, and it is a parameter the publisher never
        asked for. A publisher that refuses the parameterised URL and
        serves the plain one is not unusual, and the plain one is the
        URL the publisher itself advertises.
        """
        return url.split("?", 1)[0]

    async def _read_refused_feed(
        self,
        url: str,
        status: int,
        client: httpx.AsyncClient,
        headers: dict,
    ) -> str | None:
        """
        Read a feed the publisher refused, and return its body.

        Three ways, in this order, and the cheapest first:

            1. the feed without its query string, asked of the
               publisher directly. One request, no reader budget, and
               it is the URL the publisher itself advertises;
            2. the refused URL, read through the reader proxy. The
               reader is an address these publishers do not refuse;
            3. the bare URL, read through the reader proxy.

        Returns the feed's body, or None when none of the three
        answered. It never raises: a fallback that raises is not a
        fallback.
        """
        bare = self._bare_feed_url(url)

        if bare != url:
            try:
                if await self.check_allowed(bare):
                    await self.rate_limiter.wait()
                    response = await client.get(bare, headers=headers)
                    if response.status_code < 400 and response.text.strip():
                        logger.info(
                            "%s refused %s (status %s); the feed without "
                            "its query string was served normally.",
                            self.source.get("name"), url, status,
                        )
                        return response.text
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "Could not fetch %s without its query string: %s",
                    bare[:120], exc,
                )

        if not getattr(self.settings, "reader_proxy_enabled", True):
            return None

        candidates = [url] if bare == url else [url, bare]

        for candidate in candidates:
            text = await fetch_via_reader(
                candidate,
                client,
                proxy=self.settings.reader_proxy_url,
            )

            if text and text.strip():
                logger.info(
                    "%s refused %s (status %s); the feed was read "
                    "through the reader instead.",
                    self.source.get("name"), url, status,
                )
                return text

        # The reader could not fetch it either. Read it OURSELVES: one
        # request, no key, no quota. It cannot reach a publisher that
        # refuses this address, but it can reach a feed the reader was
        # too busy to fetch, and it is the tier that cannot expire.
        for candidate in candidates:
            text = await local_reader.fetch_html(candidate, client)
            if text and text.strip():
                logger.info(
                    "%s refused %s (status %s); the feed was read "
                    "ourselves, with no reader in the middle.",
                    self.source.get("name"), url, status,
                )
                return text

        return None

    def parse(
        self,
        raw: str,
    ) -> list[dict]:
        """
        Parse RSS/Atom using feedparser.

        Normally there is one document. When the operator asked for
        more pages of a WordPress feed there is one per page, and
        their entries are merged here, with a story that appears on
        two pages kept only once.

        A document that is not well-formed XML is REPAIRED first - see
        _parse_feed_document - because a publisher's stray control
        character is not a reason to lose its headlines. The error is
        reported only when the repaired document still cannot be read.
        """

        documents = getattr(self, "_documents", None) or [raw]

        entries: list[dict] = []
        seen: set[str] = set()
        bozo_error = None

        for document in documents:
            parsed, _repaired, original_error = _parse_feed_document(document)

            # feedparser can report malformed XML.
            if getattr(
                parsed,
                "bozo",
                False,
            ):
                # bozo_exception doesn't always mean the feed
                # is unusable, so don't automatically reject it.
                # We only reject when there are no usable entries.
                if not parsed.entries:
                    # One malformed page must not lose the pages
                    # that did work, so the error is remembered and
                    # raised only if nothing at all could be read.
                    if bozo_error is None:
                        bozo_error = getattr(
                            parsed,
                            "bozo_exception",
                            None,
                        )
                    continue

            if not parsed.entries:
                # The repaired document holds no entries either. The
                # fault to report is the fault the PUBLISHER's
                # document had, not the fault of the copy we made.
                if bozo_error is None:
                    bozo_error = original_error
                continue

            for entry in parsed.entries:
                item = dict(entry)

                # The same story can appear on two pages of a feed.
                key = item.get("id") or item.get("link")

                if key:
                    if key in seen:
                        continue
                    seen.add(key)

                entries.append(item)

        if not entries and bozo_error is not None:
            raise ValueError(
                f"Unable to parse RSS/Atom feed: "
                f"{bozo_error}"
            )

        return entries

    def normalize(
        self,
        entries: list[dict],
    ) -> list[NormalizedArticle]:

        out: list[NormalizedArticle] = []

        feed_url = self.source.get(
            "feed_url",
            "",
        )

        for entry in entries:

            # -------------------------------------------------
            # URL
            # -------------------------------------------------
            link = _get_entry_url(
                entry,
                feed_url,
            )

            # -------------------------------------------------
            # TITLE
            # -------------------------------------------------
            title = entry.get(
                "title"
            )

            if not link or not title:
                continue

            title = _strip_html(
                str(title)
            )

            if not title:
                continue

            # -------------------------------------------------
            # DATE
            # -------------------------------------------------
            published = _get_published_date(
                entry
            )

            # -------------------------------------------------
            # EXCERPT
            # -------------------------------------------------
            excerpt_source = (
                entry.get("summary")
                or entry.get("description")
            )

            if not excerpt_source:
                content = entry.get(
                    "content"
                )

                if isinstance(
                    content,
                    list,
                ) and content:

                    first_content = content[0]

                    if isinstance(
                        first_content,
                        dict,
                    ):
                        excerpt_source = (
                            first_content.get(
                                "value"
                            )
                        )

            excerpt = _strip_html(
                excerpt_source
            )

            # Prevent extremely large feed summaries.
            if excerpt:
                excerpt = excerpt[:2000].strip()

            # -------------------------------------------------
            # IMAGE
            # -------------------------------------------------
            thumbnail = _extract_thumbnail(
                entry,
                feed_url,
            )

            # -------------------------------------------------
            # VIDEO
            # -------------------------------------------------
            video_url = _extract_video_url(
                entry,
                feed_url,
            )

            # -------------------------------------------------
            # AUTHOR
            # -------------------------------------------------
            author = (
                entry.get("author")
                or entry.get("dc_creator")
            )

            if author:
                author = _strip_html(
                    str(author)
                )

            # -------------------------------------------------
            # NORMALIZED TITLE
            # -------------------------------------------------
            title_normalized = (
                _normalize_title(
                    title
                )
            )

            # -------------------------------------------------
            # CANONICAL URL
            # -------------------------------------------------
            canonical_url = (
                link.split(
                    "?",
                    1,
                )[0]
            )

            # -------------------------------------------------
            # METADATA
            # -------------------------------------------------
            metadata = {
                "content_hash": _content_hash(
                    canonical_url,
                    title_normalized,
                ),

                "title_normalized": (
                    title_normalized
                ),

                "image_url": thumbnail,

                "video_url": video_url,

                "feed_url": feed_url,

                "guid": entry.get(
                    "id"
                )
                or entry.get(
                    "guid"
                ),

                "source_updated": (
                    entry.get(
                        "updated"
                    )
                ),
            }

            # -------------------------------------------------
            # NORMALIZED ARTICLE
            # -------------------------------------------------
            out.append(
                NormalizedArticle(
                    source_article_url=link,

                    canonical_url=canonical_url,

                    headline=title.strip(),

                    published_at=published,

                    author_name=author,

                    excerpt=excerpt,

                    thumbnail_url=thumbnail,

                    # Not set here on purpose. normalize() runs before
                    # any page has been fetched, so there is nothing to
                    # set it from. It is populated later, per article,
                    # from the page media enrichment already fetches —
                    # see app/sources/body.py, which is also where
                    # republish_permission is enforced a second time.
                    full_body_html=None,

                    language=self.source.get(
                        "default_language",
                        "ne",
                    ),

                    raw_metadata=metadata,
                )
            )

        return out
