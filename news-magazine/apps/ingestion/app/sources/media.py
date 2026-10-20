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
- Every candidate image is PROVEN loadable before it is stored, via
  ImageValidator below. Selecting an image by URL shape is guesswork:
  the guess is wrong often enough that cards shipped carrying a URL
  which 403s or 404s forever, and the only party that ever found out
  was the reader's browser.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
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

# In-body images outrank og:image by this much. A 1200x675 declared
# og:image scores ~2737, so the bonus has to clear that comfortably.
#
# The Ministry of Foreign Affairs feed sets og:image to its own channel
# logo, so every story from them rendered the same "IICT Samachar"
# picture. An image found inside the article body is the photograph the
# story is actually about, so it is preferred even when the declared
# og:image is larger.
CONTENT_IMAGE_BONUS = 5000

# Container classes/ids whose <img> tags belong to the article body.
_ARTICLE_REGION_RE = re.compile(
    r"<(?P<tag>article|div|section)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*"
    r"(?:article|story|post-content|entry-content|news-content|content-body|"
    r"detail|main[-_]?content|body[-_]?content)"
    r"[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


def _article_region(html: str) -> str:
    """
    Narrow the page to the article body for image harvesting.

    Falls back to the whole document when no article container is
    marked up, because a wrong-region image beats no image.
    """
    match = _ARTICLE_REGION_RE.search(html)
    if match and len(match.group("body")) > 400:
        return match.group("body")
    return html


# Containers whose <img> tags are site furniture — banners, ads, share
# rails, related-story strips — not the story's photograph.
_CHROME_CONTAINER_RE = re.compile(
    r"<(?:div|section|aside|nav|header|footer|ul)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*"
    r"(?:banner|advert|ad-|-ad|ads|share|social|related|recommend|promo|"
    r"sidebar|widget|newsletter|subscribe|popup|modal|comment|breaking|"
    r"trending|popular|most-read|footer|header|nav|menu|logo|brand)"
    r"[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)


def _chrome_spans(html: str) -> list[tuple[int, int]]:
    """
    Character ranges occupied by page-chrome containers.

    Returned as offsets rather than deleted from the markup. An earlier
    version removed them with a regex and, because real-world HTML is
    not balanced, that deleted all 1,049 characters of Kathmandu Post's
    article. Offsets cannot corrupt the document.
    """
    spans: list[tuple[int, int]] = []

    for match in _CHROME_CONTAINER_RE.finditer(html):
        start = match.end()
        # Walk forward to this container's matching close tag, tracking
        # nesting depth so a nested div does not end it early.
        tag = match.group(0)[1:].split()[0].rstrip(">").lower()
        depth = 1
        position = start
        pattern = re.compile(
            rf"<{tag}\b[^>]*>|</{tag}\s*>", re.IGNORECASE
        )
        while depth > 0 and position < len(html):
            inner = pattern.search(html, position)
            if not inner:
                break
            depth += -1 if inner.group(0).startswith("</") else 1
            position = inner.end()
        spans.append((start, position))

    return spans


def _inside_any(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(low <= start < high for low, high in spans)

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
    r"(?:^|[/_.-])(?:pixel|spacer|blank|transparent|1x1|avatar|author|logo|"
    r"icon|sprite|favicon|placeholder|loader|lazy[-_.]?|lazyload|"
    r"default[-_]?image|no[-_]?image|dummy|mock|grey|gray|loading|"
    r"preload|skeleton|shimmer|lightbox|thumb[-_]?small)[/_.-]",
    re.IGNORECASE,
)

# An image URL must end in a filename. A path that stops at a directory
# — `.../uploads/source/` — is a base URL the scraper truncated, and the
# CDN answers it 403. It is not a rendition that happened to fail: there
# is no file behind it at any size, so no rendition fallback can rescue
# it and the card is dead from the moment it is stored.
_IMAGE_FILE_RE = re.compile(
    r"\.(?:jpe?g|png|webp|gif|avif|bmp|svg)(?:$|[?#])", re.IGNORECASE
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

# A real Chrome string. Article pages 404 on a bare curl UA often enough
# that this one is worth hard-coding, and an image CDN that answers a
# bot string with a challenge page is exactly the false negative the
# liveness probe must not walk into.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ExtractedMedia:
    """Everything we could pull off one article page."""

    image_url: str | None = None
    image_highres_url: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_alt: str | None = None
    # Every image URL the page offered, best first, deduplicated.
    #
    # Only image_url was kept before, so when the winner turned out to
    # be a dead rendition the article lost its artwork entirely — even
    # though the page had offered two or three live alternates sitting
    # right behind it. Keeping the full ranking is what lets the
    # validator fall through to the next candidate instead of giving up.
    image_candidates: list[str] = field(default_factory=list)
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

    # Client-rendered sites ship their template source. An <img> inside
    # one has src="{{data.tags[i].trending_image}}" — not a URL at all,
    # and rendering it as an image src produced a broken tile. Reject
    # anything carrying a template marker.
    if any(token in value for token in ("{{", "}}", "${", "<%", "%>", "[[", "]]")):
        return None

    host = (parsed.hostname or "").lower()
    if not host or host in _RESERVED_HOSTS:
        return None

    # Upgrade to https: mixed content is blocked on the production host.
    if parsed.scheme == "http":
        value = "https://" + value[len("http://"):]

    return value


def _looks_like_junk_image(url: str) -> bool:
    if _JUNK_IMAGE_RE.search(url):
        return True

    # No filename means no file. This is the one failure a rendition
    # fallback cannot fix, so it is judged before anything is stored
    # rather than discovered by a reader on a dead card.
    stripped = url.split("?")[0].split("#")[0].rstrip("/")
    if not stripped or "/" not in stripped.split("://", 1)[-1]:
        return True
    return not _IMAGE_FILE_RE.search(stripped)


# Proxies and resizers. These are disqualifying, not merely penalised.
#
# Kathmandu Post serves body images through
# assets-api.../thumb.php?src=<real file>, which scores well on the src=
# size token while serving a downscaled render. A -4000 penalty was not
# enough: the proxy still won, and because the proxy URL is derived from
# a handful of shared endpoints, the SAME picture ended up on eight
# unrelated cards.
#
# A resizer can only ever return a downscaled version of something else,
# so a resizer is never the best answer to "which image represents this
# article". Excluded outright rather than ranked down.
#
# The list is deliberately narrow. A blanket `/cache/` exclusion used to
# sit here as a mere penalty; promoting it to disqualifying would also
# throw away perfectly good CDN paths (Cloudflare's `/cdn-cgi/cache/`
# among them) and manufacture the blank cards this is meant to prevent.
# Only endpoints that unambiguously resize something are listed.
_RESIZER_MARKERS = (
    "thumb.php",
    "thumbnail.php",
    "/thumb/",
    "resize.php",
    "/resize/",
    "image-resize",
    "/image/resize/",
    "cdn-cgi/image",
    "wsrv.nl",
    "images.weserv.nl",
)


def _is_resizer(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _RESIZER_MARKERS)


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

    # On-the-fly thumbnail proxies. See _is_resizer: these are
    # disqualifying. Scored to -1 so any real candidate outranks them,
    # but not negative enough to make one win on a tie.
    if _is_resizer(url):
        score -= 100_000

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
                "User-Agent": BROWSER_USER_AGENT,
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

        # In-body images, gathered from the article container only.
        #
        # These are weighted ABOVE og:image. Several Nepali outlets —
        # the Ministry of Foreign Affairs feed being the clearest case —
        # set og:image to the channel's own logo, so every story from
        # them renders the same picture. The photograph inside the body is
        # the story's actual image, so it wins.
        body_html = html
        chrome = _chrome_spans(html)
        for match in re.finditer(
            r'<img\b[^>]*\bsrc(?:set)?\s*=\s*["\']([^"\']+)["\'][^>]*>', body_html, re.IGNORECASE
        ):
            # Skip anything sitting inside a banner / ad / share / related
            # container. Those are the images that repeat across every
            # story on the site.
            if _inside_any((match.start(), match.end()), chrome):
                continue
            src = _clean_url(match.group(1), final_url)
            if not src or _looks_like_junk_image(src):
                continue
            image_candidates.append(
                (_score_image(src, None, None) + CONTENT_IMAGE_BONUS, src, "body")
            )

        if image_candidates:
            scored = sorted(image_candidates, key=lambda item: item[0], reverse=True)
            best_score, best_url, best_source = scored[0]
            result.image_url = best_url
            result.image_highres_url = best_url
            result.provenance["image_url"] = best_source

            # Keep the whole ranking, deduplicated in score order, so a
            # rendition that fails validation can be swapped for the next
            # one the same page already offered.
            ranked: list[str] = []
            for _, url, _ in scored:
                if url not in ranked:
                    ranked.append(url)
            result.image_candidates = ranked

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


# Size suffixes the web card strips before trying the next rendition.
# Kept byte-for-byte in step with withoutSizeSuffix() in
# apps/web/components/NewsCard.tsx: when a "-1200x675" rendition is dead
# the original file at its own path is usually alive, and the browser
# already knew that. Doing it server-side means the reader never sees
# the failed request in the first place.
_SIZE_SUFFIX_PATTERNS = (
    re.compile(r"[-_]\d{2,4}x\d{2,4}(?=\.(?:jpg|jpeg|png|webp))", re.IGNORECASE),
    re.compile(
        r"[-_](?:w|width|resize)[-_]?\d{2,4}(?=[x_.]|\.(?:jpg|jpeg|png|webp))",
        re.IGNORECASE,
    ),
    re.compile(r"[?&]w=\d{2,4}\b", re.IGNORECASE),
)


def strip_size_suffix(url: str) -> str:
    """The same URL with any width token removed. Unchanged if none."""
    for pattern in _SIZE_SUFFIX_PATTERNS:
        stripped = pattern.sub("", url)
        if stripped != url:
            return stripped
    return url


def candidate_images(feed_url: str | None, page: ExtractedMedia | None) -> list[str]:
    """
    Every image rendition worth trying, best first, deduplicated.

    Ordering mirrors the web card exactly — page ranking first, feed
    thumbnail second, and only then the size-stripped variants of each.
    Preferring a real rendition over a stripped guess matters: the
    stripped form is the fallback, not the favourite.

    Resizers and junk are dropped here rather than merely ranked down.
    A resizer can only return a downscaled version of something else,
    and because proxy URLs are built from a handful of shared endpoints
    the same picture used to land on eight unrelated cards. No image
    beats a downscaled stand-in: the card has an honest "no image
    available" state and the feed demotes imageless stories.
    """
    if page is not None and page.image_candidates:
        ranked = list(page.image_candidates)
    elif page is not None:
        ranked = [u for u in (page.image_highres_url, page.image_url) if u]
    else:
        ranked = []

    if feed_url:
        ranked.append(feed_url)

    ordered: list[str] = []

    def push(url: str) -> None:
        if not url or _is_resizer(url) or _looks_like_junk_image(url):
            return
        if url not in ordered:
            ordered.append(url)

    for url in ranked:
        push(url)
    for url in ranked:
        push(strip_size_suffix(url))

    return ordered


def best_image(feed_url: str | None, page: ExtractedMedia) -> str | None:
    """
    Decide the image to store, without touching the network.

    A page-level og:image beats a feed thumbnail even when the thumbnail
    looks larger, because the og:image is the one the outlet chose for
    sharing. The feed URL is kept only as the fallback.

    This is the shape of the answer, not the proof. `resolve_live_image`
    below walks the same list and confirms which one actually loads.
    """
    candidates = candidate_images(feed_url, page)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Image liveness
#
# Everything above this line decides an image from the URL's *shape*:
# declared width, a size token, the tag it came from. That is a good
# guess and a terrible guarantee. A CDN can answer 403 to every referrer
# it does not recognise — assets-cdn.kathmandupost.com does exactly that
# — and the row was written anyway, so the feed carried a permanently
# dead URL and the only thing that ever discovered it was a reader's
# browser, on their screen, after the card had already rendered.
#
# So before a URL is stored it is asked to prove itself, with a request
# built to look like the one the browser will make.
# ---------------------------------------------------------------------------

# Content types a browser <img> can paint. A 200 is not enough on its
# own: a CDN under load happily answers 200 with an HTML error page or
# a JSON manifest, and that is still a broken card.
#
# image/svg+xml is deliberately absent. It is renderable in principle,
# but on these outlets an SVG in a news slot is a logo or an ad rather
# than a photograph, and _looks_like_junk_image already targets that
# family. Storing one would be a visually correct way to put furniture
# on the front page.
RENDERABLE_IMAGE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/pjpeg",
        "image/png",
        "image/apng",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/tiff",
        "image/heic",
        "image/heif",
    }
)

# How many bytes to pull. Enough to tell a real image from an HTML
# error page, small enough that proving every thumbnail in a poll costs
# kilobytes rather than megabytes. Servers honouring the Range answer
# with exactly this many bytes; the rest never read the body at all.
IMAGE_PROBE_BYTES = 1024

# A 200 whose first byte is '<' is an error page or a bot challenge, not
# an image. Cloudflare in front of an origin that 403s is the common
# source of one of these, and it is precisely the failure a Content-Type
# check alone lets through.
_MARKUP_SNIFF = re.compile(rb"^\s*(?:<!doctype|<html|<head|<body|<)", re.IGNORECASE)


def _content_type(response: httpx.Response) -> str:
    """The media type alone, lowercased, with any parameters dropped."""
    raw = response.headers.get("content-type", "") or ""
    return raw.split(";", 1)[0].strip().lower()


class ImageValidator:
    """
    Proves an image URL returns an image, and remembers the answer.

    Two decisions make this affordable inside a poll of 27 outlets:

    - **The request mirrors the browser's.** A ranged GET rather than a
      bare HEAD, because CDNs answer the two differently often enough
      that a HEAD-based check produces confident false negatives on
      perfectly good images. No Referer, matching the web card's
      referrerPolicy="no-referrer", because several of these CDNs reject
      a foreign Referer outright — sending one would prove the image
      dead precisely when it is alive for readers.

    - **Results are cached per URL.** Outlets reuse one image across
      stories: a channel logo, a standing photo, an ad that escaped the
      chrome filter. Without the cache each of those is re-requested for
      every article that shares it, and the poll cost scales with
      articles instead of with distinct images.

    Never raises. An unreachable CDN is an image that does not get
    stored, not a sync that dies.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        timeout: float = 6.0,
        cache_size: int = 2048,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._cache_size = max(cache_size, 1)
        self._cache: OrderedDict[str, bool] = OrderedDict()
        # Counted so a poll can report what validation actually cost.
        self.requests = 0
        self.cache_hits = 0

    def cached(self, url: str) -> bool | None:
        """The remembered verdict for a URL, or None if never probed."""
        return self._cache.get(url)

    async def is_live(self, url: str | None) -> bool:
        """True only if this URL is proven to return a renderable image."""
        if not url:
            return False

        remembered = self._cache.get(url)
        if remembered is not None:
            self._cache.move_to_end(url)
            self.cache_hits += 1
            return remembered

        live = await self._probe(url)
        self._cache[url] = live
        # Bounded: a long-running poll must not accumulate verdicts for
        # every URL it has ever seen. Oldest verdict goes first, which
        # for a single poll is the right thing to forget.
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return live

    async def _probe(self, url: str) -> bool:
        self.requests += 1
        try:
            async with self._client.stream(
                "GET",
                url,
                headers={
                    "User-Agent": BROWSER_USER_AGENT,
                    # What Chrome sends for an <img>. Asking for modern
                    # formats lets a CDN that does content negotiation
                    # answer with WebP, which is what the reader gets.
                    "Accept": (
                        "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                    ),
                    # Cap the transfer. A server that ignores this sends
                    # the whole file, so the body is read only to the
                    # byte budget below and the connection is then
                    # dropped unread.
                    "Range": f"bytes=0-{IMAGE_PROBE_BYTES - 1}",
                },
                timeout=self._timeout,
                follow_redirects=True,
            ) as response:
                # 206 is the correct answer to a ranged GET; 200 means
                # the server ignored the Range and is sending the whole
                # file. Both are a live image. Anything else — 403 from
                # a referer sniff, 404, 429 — is not.
                if response.status_code not in (200, 206):
                    logger.debug(
                        "image rejected: %s answered %s", url, response.status_code
                    )
                    return False

                content_type = _content_type(response)
                if content_type not in RENDERABLE_IMAGE_TYPES:
                    logger.debug(
                        "image rejected: %s served %r", url, content_type or "no type"
                    )
                    return False

                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) <= 0:
                            logger.debug("image rejected: %s served 0 bytes", url)
                            return False
                    except ValueError:
                        pass

                head = b""
                async for chunk in response.aiter_bytes():
                    head += chunk
                    if len(head) >= IMAGE_PROBE_BYTES:
                        break

                if not head:
                    logger.debug("image rejected: %s served an empty body", url)
                    return False
                if _MARKUP_SNIFF.match(head):
                    logger.debug(
                        "image rejected: %s served markup as %s", url, content_type
                    )
                    return False

                return True

        except Exception as exc:  # noqa: BLE001 — a dead image is not an error
            logger.debug("image probe failed for %s: %s", url, exc)
            return False


async def resolve_live_image(
    candidates: list[str],
    validator: ImageValidator,
    limit: int = 3,
) -> str | None:
    """
    The first candidate that proves it loads, or None.

    Walks the list in rank order and stops at the first success, so the
    common case costs exactly one request per NEW image. `limit` caps
    the damage when every candidate is dead: a pathological page cannot
    turn one article into an unbounded burst of requests against a CDN
    that is already refusing to serve us.
    """
    for url in candidates[: max(limit, 0)]:
        if await validator.is_live(url):
            return url
    return None


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