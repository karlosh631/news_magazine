"""
High-definition media extraction from an article page.

Stage 1 only ever read media URLs that the RSS feed happened to carry.
Most Nepali outlets publish a perfectly good og:image and an embedded
YouTube video that never appear in the feed at all — which is why the
site rendered blank image tiles. This module fetches the article page
itself and pulls out:

    - the highest-resolution image actually on offer
    - embedded video (YouTube / Vimeo / native <video>)
    - audio / podcast streams
    - the og:title / og:description / article:published_time fallbacks

Design constraints:

- Never raises. A page that 500s, times out, or has no media just
  yields empty fields; the article still gets stored.
- Never fetches more than one page per article, and only after the
  feed already told us the article exists — this is a bounded extra
  request, not a crawl.
- Honours the same per-host rate limiter the feed fetch uses.
- Records provenance (where each URL came from) so the DB can tell a
  genuine og:image from a 1x1 tracking pixel.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

try:
    import trafilatura

    TRAFILATURA_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by requirements.txt
    trafilatura = None  # type: ignore[assignment]
    TRAFILATURA_AVAILABLE = False

logger = logging.getLogger("media")

# Below this many characters the page is a stub, a cookie banner, or a
# JS shell rather than an article. 120 rather than 200 because a short
# bulletin is still a real story worth summarising.
MIN_ARTICLE_TEXT_CHARS = 120

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:embed|watch\?v=|shorts/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.IGNORECASE)
FB_RE = re.compile(r"facebook\.com/[^/]+/videos/[^/]+/(\d+)", re.IGNORECASE)

# Image URLs that are tracking pixels, spacers, logos, sprites, or
# otherwise useless as article artwork.
_JUNK_IMAGE_RE = re.compile(
    r"(?:^|[/_.-])(?:pixel|spacer|blank|transparent|1x1|avatar|logo|"
    r"icon|sprite|favicon|placeholder|loader|default[-_]?image)[/_.-]",
    re.IGNORECASE,
)

# Common image-URL size tokens. When several candidates exist we prefer
# the one asking for the largest rendition.
_SIZE_TOKEN_RE = re.compile(r"[-_/](?:w|width)?[-_]?(\d{2,4})(?=[x_/.-]|\.(?:jpg|jpeg|png|webp))", re.IGNORECASE)

_RESERVED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}


@dataclass
class ExtractedMedia:
    """Everything we could pull off one article page."""

    image_url: str | None = None
    image_highres_url: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_alt: str | None = None
    video_embed_url: str | None = None
    video_id: str | None = None
    video_thumbnail_url: str | None = None
    audio_url: str | None = None
    audio_description: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    author: str | None = None
    word_count: int | None = None
    # Plain article text, used only to feed the extractive summariser.
    # Never stored as body_html — the outlet did not license its body.
    article_text: str | None = None
    # Which source each field came from, e.g. {"image_url": "og:image"}.
    # Stored so we can debug "why is this image wrong" without re-fetching.
    provenance: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            (
                self.image_url,
                self.video_embed_url,
                self.audio_url,
                self.title,
                self.description,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_url(value: str | None, base_url: str) -> str | None:
    """
    Normalise a possibly-relative, possibly-srcset image reference.

    Two fixes live here because both produced URLs that resolved to
    nothing in a browser:

    1. "<url> 2560w, <url> 1200w" — several outlets put a whole srcset
       in og:image. Passing that through yields a URL with a space and a
       comma in it, which is not a valid URL and renders as a broken
       image. Take the first candidate and its width.

    2. http:// URLs. 38 of the stored images came back over plain http,
       which a browser blocks as mixed content the moment the page is
       served over https — so they worked on localhost and broke on
       production. Upgrade to https.
    """
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None

    # srcset payload -> first candidate.
    if "," in value and re.search(r"\d+\s*w", value):
        first = value.split(",")[0].strip().split()
        if first:
            value = first[0]

    if value.startswith("//"):
        value = "https:" + value
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower()
    if not host or host in _RESERVED_HOSTS:
        return None

    # Upgrade to https: mixed content is blocked on the production host.
    if parsed.scheme == "http":
        value = "https://" + value[len("http://"):]

    return value


def _looks_like_junk_image(url: str) -> bool:
    return bool(_JUNK_IMAGE_RE.search(url))


def _score_image(url: str, declared_width: int | None, declared_height: int | None) -> int:
    """
    Higher is better. Combines any declared dimensions with the size
    token baked into the URL so we prefer e.g. ...-1200x0.jpg over
    ...-320x180.jpg even when only one of them advertises a width.
    """
    score = 0
    if declared_width:
        score += min(declared_width, 2000)
    if declared_height:
        score += min(declared_height, 2000) // 2

    tokens = _SIZE_TOKEN_RE.findall(url)
    if tokens:
        try:
            score += max(int(t) for t in tokens)
        except ValueError:
            pass

    # Prefer modern formats at equal size.
    if url.lower().split("?")[0].endswith((".webp", ".avif")):
        score += 40

    return score


def _parse_dimensions(meta: dict[str, str], prefix: str) -> tuple[int | None, int | None]:
    try:
        width = int(meta.get(f"{prefix}:width", "") or 0) or None
        height = int(meta.get(f"{prefix}:height", "") or 0) or None
    except (TypeError, ValueError):
        return None, None
    return width, height


def _extract_meta_tags(html: str) -> dict[str, str]:
    """
    Pull every <meta property/name content="..."> into a dict.

    Deliberately regex-based rather than BeautifulSoup: it runs on every
    article page, is allocation-light, and meta tags have no nesting so
    the parse is unambiguous. Attribute order is not fixed across
    sites, hence the two-order attempts.
    """
    meta: dict[str, str] = {}

    for tag in re.findall(r"<meta\b[^>]*>", html, re.IGNORECASE):
        key_m = re.search(
            r'\b(?:property|name|itemprop)\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE
        )
        content_m = re.search(r'\bcontent\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if not key_m or not content_m:
            continue
        key = key_m.group(1).strip().lower()
        content = content_m.group(1).strip()
        if key and content:
            meta.setdefault(key, content)

    return meta


def _extract_link_rel(html: str, rel_values: tuple[str, ...], base_url: str) -> str | None:
    for rel in rel_values:
        pattern = re.compile(
            r'<link\b[^>]*\brel\s*=\s*["\']' + re.escape(rel) + r'["\'][^>]*>', re.IGNORECASE
        )
        for tag in pattern.findall(html):
            href = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if href:
                cleaned = _clean_url(href.group(1), base_url)
                if cleaned:
                    return cleaned
    return None


def _find_youtube(html: str) -> tuple[str | None, str | None]:
    """Return (video_id, canonical embed URL)."""
    for pattern in (YOUTUBE_RE, VIMEO_RE, FB_RE):
        match = pattern.search(html)
        if match:
            video_id = match.group(1)
            if pattern is YOUTUBE_RE:
                return video_id, f"https://www.youtube-nocookie.com/embed/{video_id}"
            if pattern is VIMEO_RE:
                return video_id, f"https://player.vimeo.com/video/{video_id}"
            return video_id, None
    return None, None


def _find_audio(html: str, base_url: str) -> tuple[str | None, str | None]:
    """
    Return (audio_url, description).

    Looks for <audio src>, <source src> inside <audio>, <enclosure> with
    an audio MIME type, and the common podcast <iframe> patterns.
    """
    audio_mime = re.compile(
        r"<(?:audio|source|enclosure)\b[^>]*"
        r"(?:type\s*=\s*[\"'](?:audio/|application/ogg|audio%2F))?[^>]*"
        r"\bsrc\s*=\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    for match in audio_mime.finditer(html):
        url = _clean_url(match.group(1), base_url)
        if url and not _looks_like_junk_image(url):
            return url, "audio element"

    enclosure = re.compile(
        r'<enclosure\b[^>]*url\s*=\s*["\']([^"\']+)["\'][^>]*>'
        r'[^<]*?type\s*=\s*["\'](audio/[^"\']+)["\']',
        re.IGNORECASE,
    )
    match = enclosure.search(html)
    if match:
        url = _clean_url(match.group(1), base_url)
        if url:
            return url, f"rss enclosure ({match.group(2)})"

    podcast = re.compile(
        r'<iframe\b[^>]*src\s*=\s*["\'](https://[^"\']*(?:anchor\.fm|'
        r'soundcloud\.com|buzzsprout\.com|simplecast\.com|megaphone\.fm|'
        r'spreaker\.com|player\.podbean\.com|podcasts\.apple\.com)[^"\']*)["\']',
        re.IGNORECASE,
    )
    match = podcast.search(html)
    if match:
        return _clean_url(match.group(1), base_url), "podcast embed"

    return None, None


def _count_words(html: str) -> int | None:
    """
    Rough article length from the <article> / <p> block. Used only to
    skip scraping paywalled stubs and to size excerpts, so an
    approximation is fine.
    """
    text = extract_article_text(html)
    if not text:
        return None
    words = text.split()
    return len(words) if len(words) > 20 else None


def extract_article_text(html: str, max_chars: int = 20_000) -> str | None:
    """
    Pull the article's prose out of the page markup.

    Feeds the extractive summariser, which needs real sentences from the
    outlet. Nothing generated — this is the outlet's own text.

    Uses trafilatura as the primary extractor because it is built for
    exactly this job: it scores blocks by paragraph density and text
    length, so navigation, share widgets, related-story rails and ad
    slots fall away on their own.

    The earlier hand-rolled version stripped <div class="...social...">
    regions with a regex, and on Kathmandu Post that regex removed all
    1,049 characters of real article text because the page's div nesting
    is not balanced. Never strip by CSS class on real-world HTML.

    Falls back to <p>-tag harvesting when trafilatura declines the page.
    """
    if not html:
        return None

    if TRAFILATURA_AVAILABLE:
        try:
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
                no_fallback=False,
            )
            if extracted and len(extracted) > MIN_ARTICLE_TEXT_CHARS:
                return extracted[:max_chars]
        except Exception:
            logger.debug("trafilatura extraction failed; using <p> fallback")

    text = re.sub(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)

    blocks = re.findall(r"<p\b[^>]*>(.*?)</p>", text, re.IGNORECASE | re.DOTALL)
    joined = " ".join(blocks)
    joined = re.sub(r"<[^>]+>", " ", joined)
    joined = re.sub(r"\s+", " ", joined).strip()

    return joined[:max_chars] if len(joined) > MIN_ARTICLE_TEXT_CHARS else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_from_article(
    url: str,
    client: httpx.AsyncClient,
    referer: str | None = None,
    max_bytes: int = 3_000_000,
) -> ExtractedMedia:
    """
    Fetch one article page and extract HD media.

    Args:
        url: The article URL. Must already have passed robots.txt.
        client: A caller-owned httpx client so connections are pooled
                across a whole sync run rather than one per article.
        referer: Sent as Referer because several Nepali CDNs refuse
                hot-linked images without it.

    Never raises. Returns an empty ExtractedMedia on any failure.
    """
    result = ExtractedMedia()

    if not url:
        return result

    try:
        response = await client.get(
            url,
            headers={
                # Article pages 404 on a bare curl UA often enough that
                # this one is worth hard-coding.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ne,en;q=0.8",
                **({"Referer": referer} if referer else {}),
            },
            timeout=20.0,
            follow_redirects=True,
        )

        if response.status_code >= 400:
            logger.debug("media: %s returned %s", url, response.status_code)
            return result

        html = response.text[:max_bytes]
        final_url = str(response.url)
        meta = _extract_meta_tags(html)

        # ---------------- Image ----------------
        image_candidates: list[tuple[int, str, str]] = []  # (score, url, source)

        og_image = _clean_url(
            meta.get("og:image")
            or meta.get("og:image:secure_url")
            or meta.get("og:image:url"),
            final_url,
        )
        if og_image:
            w, h = _parse_dimensions(meta, "og:image")
            image_candidates.append((_score_image(og_image, w, h), og_image, "og:image"))

        twitter_image = _clean_url(
            meta.get("twitter:image") or meta.get("twitter:image:src"), final_url
        )
        if twitter_image:
            w, h = _parse_dimensions(meta, "twitter:image")
            image_candidates.append((_score_image(twitter_image, w, h), twitter_image, "twitter:image"))

        link_image = _extract_link_rel(
            html, ("image_src", "apple-touch-icon", "apple-touch-icon-precomposed"), final_url
        )
        if link_image:
            image_candidates.append((_score_image(link_image, None, None), link_image, "link:image_src"))

        for match in re.finditer(
            r'<img\b[^>]*\bsrc(?:set)?\s*=\s*["\']([^"\']+)["\'][^>]*>', html, re.IGNORECASE
        ):
            src = _clean_url(match.group(1), final_url)
            if not src or _looks_like_junk_image(src):
                continue
            # Skip anything inside a header/nav/footer/aside block.
            prefix = html[: match.start()].rsplit("<", 1)[-1]
            if re.match(r"(header|nav|footer|aside|form)\b", prefix, re.IGNORECASE):
                continue
            image_candidates.append((_score_image(src, None, None), src, "img"))

        if image_candidates:
            scored = sorted(image_candidates, key=lambda item: item[0], reverse=True)
            best_score, best_url, best_source = scored[0]
            result.image_url = best_url
            result.image_highres_url = best_url
            result.provenance["image_url"] = best_source

            # Only call it high-res when the page actually told us it is
            # large enough to be worth the extra bytes.
            if best_score >= 640:
                result.image_highres_url = best_url

        result.image_alt = meta.get("og:image:alt") or meta.get("twitter:image:alt")
        if not result.image_alt:
            alt = re.search(
                r'<meta\b[^>]*\bname\s*=\s*["\']twitter:image:alt["\'][^>]*'
                r'\bcontent\s*=\s*["\']([^"\']*)["\']',
                html,
                re.IGNORECASE,
            )
            result.image_alt = alt.group(1).strip() if alt else None

        # ---------------- Video ----------------
        video_id, embed_url = _find_youtube(html)
        if embed_url:
            result.video_id = video_id
            result.video_embed_url = embed_url
            result.provenance["video_embed_url"] = "iframe/embed"

        og_video = _clean_url(
            meta.get("og:video:secure_url") or meta.get("og:video:url") or meta.get("og:video"),
            final_url,
        )
        if og_video and not result.video_embed_url:
            if YOUTUBE_RE.search(og_video) or VIMEO_RE.search(og_video):
                video_id, embed_url = _find_youtube(og_video)
                if embed_url:
                    result.video_id = video_id
                    result.video_embed_url = embed_url
                    result.provenance["video_embed_url"] = "og:video"
            else:
                result.video_embed_url = og_video
                result.provenance["video_embed_url"] = "og:video"

        if video_id:
            result.video_thumbnail_url = (
                f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            )
        elif not result.video_thumbnail_url:
            og_video_img = _clean_url(meta.get("og:video:image"), final_url)
            result.video_thumbnail_url = og_video_img

        # ---------------- Audio ----------------
        audio_url, audio_desc = _find_audio(html, final_url)
        if audio_url:
            result.audio_url = audio_url
            result.audio_description = audio_desc
            result.provenance["audio_url"] = audio_desc or "audio element"

        # ---------------- Text fallbacks ----------------
        result.title = meta.get("og:title") or meta.get("twitter:title")
        result.description = meta.get("og:description") or meta.get("twitter:description")
        result.published_at = (
            meta.get("article:published_time")
            or meta.get("og:article:published_time")
            or meta.get("datepublished")
            or meta.get("date")
        )
        result.author = (
            meta.get("author")
            or meta.get("article:author")
            or meta.get("byl")
            or meta.get("twitter:creator")
        )
        result.canonical_url = _extract_link_rel(html, ("canonical", "alternate"), final_url)
        result.article_text = extract_article_text(html)
        result.word_count = _count_words(html)

        return result

    except Exception as exc:  # noqa: BLE001 — extraction must never break a sync
        logger.debug("media: extraction failed for %s: %s", url, exc)
        return result


def best_image(feed_url: str | None, page: ExtractedMedia) -> str | None:
    """
    Decide the image to store.

    A page-level og:image beats a feed thumbnail even when the thumbnail
    looks larger, because the og:image is the one the outlet chose for
    sharing. The feed URL is kept only as the fallback.
    """
    return page.image_url or feed_url


def upgrade_to_highres(url: str | None) -> str | None:
    """
    Best-effort rewrite of a known thumbnail URL into its full-size
    sibling.

    Several outlets publish both .../image-320x180.jpg and
    .../image-1200x675.jpg from the same directory. When we only ever
    saw the small one, this recovers the large one without a re-fetch.
    Returns the original URL unchanged when no pattern matches.
    """
    if not url:
        return None

    for pattern, replacement in (
        (re.compile(r"[-_](?:w|width|thumb|thumbnail|resize)[-_]?(\d{2,4})(?=[x_.]|\.(?:jpg|jpeg|png|webp))", re.I), "-1200"),
        (re.compile(r"[-_](\d{2,3})x(\d{2,3})(?=\.(?:jpg|jpeg|png|webp))", re.I), "-1200x675"),
        (re.compile(r"[?&](?:w|width)=(\d{2,4})\b", re.I), "w=1200"),
    ):
        if pattern.search(url):
            upgraded = pattern.sub(replacement, url, count=1)
            if upgraded != url:
                return upgraded

    return url


def media_client(limits: dict[str, Any] | None = None) -> httpx.AsyncClient:
    """Shared client for a whole sync run."""
    return httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        headers={"Accept-Encoding": "gzip, deflate"},
        **(limits or {}),
    )