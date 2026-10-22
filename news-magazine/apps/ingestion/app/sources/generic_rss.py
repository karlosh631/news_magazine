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
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from app.sources.base import NormalizedArticle, SourceAdapter


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
        }

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

                response.raise_for_status()

                # Make sure we actually received content.
                if not response.text.strip():
                    raise ValueError(
                        f"RSS feed returned an empty response: "
                        f"{url}"
                    )

                documents.append(response.text)

        self._documents = documents

        return documents[0]

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
        """

        documents = getattr(self, "_documents", None) or [raw]

        entries: list[dict] = []
        seen: set[str] = set()
        bozo_error = None

        for document in documents:
            parsed = feedparser.parse(
                document
            )

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
