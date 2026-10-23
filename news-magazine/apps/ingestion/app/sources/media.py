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
from urllib.parse import unquote, urljoin, urlparse

import httpx
from app.config import get_settings
from app.sources.reader_proxy import fetch_via_reader

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

# Which <img> is this article's photograph, in order of authority.
#
# A news page offers, roughly in this order:
#
#   1. the outlet's own og:image / twitter:image - the picture it picked
#      for this story, and the one it publishes to social and to us
#   2. the story's photograph, first in reading order inside the body
#   3. a long tail of furniture: a byline portrait, a utility strip of
#      icons (flight, weather, gold rate), an ad banner, and a carousel
#      of unrelated stories
#
# The TIERS decide the answer, and they have to - nothing about a URL's
# shape can. WordPress writes the UPLOAD YEAR into the path, so
# /uploads/2026/10/meat.jpg and /uploads/2026/10/gold.png are
# indistinguishable to a scorer, and _SIZE_TOKEN_RE used to read that year
# as a pixel width. That handed the ranking to whichever picture was
# uploaded most recently, which is how a story about cooking meat for
# Dashain ended up wearing a photograph of gold jewellery.
#
# Only ties inside a tier are broken by score, so the gap between tiers is
# set wider than any score one candidate can reach.
TIER_METADATA = 3
TIER_ARTICLE_BODY = 2
TIER_PAGE = 1

TIER_STEP = 10_000

# Still a real rule, and still the fix for an outlet that sets og:image to
# its channel logo (the Ministry of Foreign Affairs did, so every story
# from them rendered the same "IICT Samachar" picture). It is applied when
# ranking body images against each other; metadata that is plainly
# furniture is skipped outright rather than out-scored, because the URL
# shape is the only evidence available before the bytes are read.
CONTENT_IMAGE_BONUS = 5000

# Container classes/ids whose <img> tags belong to the article body.
_ARTICLE_REGION_RE = re.compile(
    r"<(?P<tag>article|div|section)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*"
    r"(?:article|story|post-content|entry-content|news-content|content-body|"
    r"detail|main[-_]?content|body[-_]?content)"
    r"[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


def _article_region_span(html: str) -> tuple[int, int] | None:
    """
    Offsets of the largest article container in the page, or None.

    Returns offsets rather than the markup so a candidate can still be
    checked against the chrome spans, which are also offsets into the
    original document.

    The largest match is used because the first one is usually the wrong
    one: `class="post-detail"` on a byline toolbar matches the `detail`
    keyword and yields a 141-character div, and a 141-character div is
    not the article. On nepalnews.com no container matches at all, which
    is why the tiers below do not depend on this finding one - it only
    separates body images from the rest of the page when a container
    does exist.
    """
    best: tuple[int, int] | None = None
    for match in _ARTICLE_REGION_RE.finditer(html):
        span = match.span("body")
        if span[1] - span[0] <= 400:
            continue
        if best is None or (span[1] - span[0]) > (best[1] - best[0]):
            best = span
    return best


# Containers whose <img> tags are site furniture — banners, ads, share
# rails, related-story strips, byline blocks — not the story's photograph.
#
# `byline` / `writers-box` / `author-images` are here because a byline
# portrait is the one piece of furniture that sits INSIDE the article
# column, ahead of the photograph, and is served from the same
# /wp-content/uploads/ tree as real artwork. On nepalnews.com the byline
# is <div class="author-writer writers-box"> holding
# <img class="post-author-image" src=".../uploads/2025/08/Nepalnews.jpg">
# — a URL with no hint in it at all, which is exactly why the container
# and class checks exist.
#
# `utility` catches the icon strip a Nepali masthead hangs under its
# off-canvas menu — flight times, weather, air quality, forex, gold rate.
# On nepalnews.com it is <div class="utility-icons sidebar-utilities">,
# and the four icons that precede the gold icon are what the byline and
# related-story filters leave standing when a page carries no og:image.
_CHROME_CONTAINER_RE = re.compile(
    r"<(?:div|section|aside|nav|header|footer|ul)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*"
    r"(?:banner|advert|ad-|-ad|ads|share|social|related|recommend|promo|"
    r"sidebar|widget|newsletter|subscribe|popup|modal|comment|breaking|"
    r"trending|popular|most-read|footer|header|nav|menu|logo|brand|"
    r"byline|writers?-box|author-images?|utilit(?:y|ies))"
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
    r"preload|skeleton|shimmer|lightbox|thumb[-_]?small|statcounter|"
    r"counter|beacon|pixel|404|not[-_]?found)[/_.-]",
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

# A file inside a THEME's own asset directory is site furniture, never a
# story's photograph.
#
# `https://onlinekhabar.com/wp-content/themes/onlinekhabar-2021/img/ok-khusi.png`
# is OnlineKhabar's own "khusi" badge, and it was being served as the
# og:image of two unrelated stories. A theme directory holds the site's
# chrome — its logo, its badges, its sprites — and no publisher files a
# news photograph there.
#
# The shape is `/themes/<theme>/<asset-dir>/`, which is WordPress's
# convention and the only one in the wild where a real story image could
# be mistaken for chrome.
_THEME_ASSET_RE = re.compile(
    r"/themes?/[^/]+/(?:img|images|assets|dist|static|fonts)/", re.IGNORECASE
)

# WordPress writes a duplicate upload as "name-Copy.jpg", and publishers
# leave them behind when they re-upload a picture.
#
# `.../public/400-300/media/FBBF4C13...-1-Copy.jpg` was the artwork of
# three unrelated OnlineKhabar stories. A copy of a copy is not the
# original upload, and the original is the one the outlet intended.
_DUPLICATE_COPY_RE = re.compile(
    r"[-_](?:copy|副本|kopy|copia|copie)\.(?:jpe?g|png|webp|gif|avif)$",
    re.IGNORECASE,
)

# A GIF in the IMAGE columns is nearly always a screenshot, an animated
# badge or a sprite.
#
# `.../Clipboard_-_October_8_2024_4_59_PM.gif` is a photograph of a
# clipboard, dated October 2024, and it was the artwork of two 2026
# speeches. A publisher's own animated picture is a real thing, and it
# has its own column — `gif_url`, taken from the feed and rendered
# deliberately. So a GIF arriving as the story's photograph is furniture
# that reached the wrong field.
_GIF_ARTWORK_RE = re.compile(r"\.gif(?:$|[?#])", re.IGNORECASE)

# Common image-URL size tokens, used to prefer the largest rendition.
#
# A token only counts when the URL attaches it to a SIZE. The earlier
# version accepted any bare number between path separators, so
# /wp-content/uploads/2026/10/meat.jpg scored as 2026 pixels wide and
# /uploads/2025/01/gold.png as 2025 - the upload year, read as a width.
# Ranking then followed the calendar instead of the article, and any
# image the outlet had re-used from an older folder lost to whatever the
# site had uploaded most recently. Snakes_-1433-scaled.jpg was read as
# 1433 pixels for the same reason.
#
# Both remaining shapes say "this number is a dimension":
#   - a width keyword:  -w1200.jpg   _width_1200.jpg   /w1200/
#   - a width x height pair:  -1024x556.jpg   /1200x675/
_SIZE_TOKEN_RE = re.compile(
    r"(?:[-_]|/)(?:w|width|resize)[-_]?(\d{2,4})(?![0-9])"
    r"|[-_/](\d{2,4})x\d{2,4}(?![0-9])",
    re.IGNORECASE,
)

# The largest score a single size token is allowed to contribute. Real
# renditions stop well short of this; unbounded, a five-digit token in a
# filename would otherwise outrank a tier boundary.
MAX_SIZE_TOKEN_SCORE = 2000

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
    # The URLs the outlet DECLARED as this story's picture (og:image /
    # twitter:image), kept apart from image_candidates so a caller can
    # see whether the publisher's choice was the one that survived.
    # See resolve_live_image's `declared`.
    declared_images: list[str] = field(default_factory=list)
    video_embed_url: str | None = None
    video_id: str | None = None
    video_thumbnail_url: str | None = None
    audio_url: str | None = None
    audio_description: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    # The page this media came from, after any redirect. It is the
    # Referer a browser would send with a picture it loads from this
    # page, and several Nepali CDNs refuse a picture request that
    # carries no referer at all.
    source_url: str | None = None
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


def _is_theme_asset(url: str) -> bool:
    """A file from the site's own theme directory - chrome, not a story."""
    return bool(_THEME_ASSET_RE.search(url))


def _is_duplicate_copy(url: str) -> bool:
    """A "name-Copy" duplicate of an upload, not the original."""
    return bool(_DUPLICATE_COPY_RE.search(url.split("?")[0].split("#")[0]))


def _is_gif_artifact(url: str) -> bool:
    """A GIF in the artwork columns: a screenshot, a badge or a sprite."""
    return bool(_GIF_ARTWORK_RE.search(url.split("?")[0].split("#")[0]))


def _looks_like_junk_image(url: str) -> bool:
    if _JUNK_IMAGE_RE.search(url):
        return True

    # The three rules above are separate functions rather than more
    # alternatives in one big regex, so that a rule can be reasoned
    # about - and switched off - on its own without unravelling the
    # others. Each of them exists because a specific wrong picture was
    # measured on production.
    if _is_theme_asset(url) or _is_duplicate_copy(url) or _is_gif_artifact(url):
        return True

    # No filename means no file. This is the one failure a rendition
    # fallback cannot fix, so it is judged before anything is stored
    # rather than discovered by a reader on a dead card.
    stripped = url.split("?")[0].split("#")[0].rstrip("/")
    if not stripped or "/" not in stripped.split("://", 1)[-1]:
        return True
    return not _IMAGE_FILE_RE.search(stripped)


# An <img> tag, and the attributes that say what it is.
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_ATTR_RE = re.compile(
    r"\b(class|alt|id|title|width|height)\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE
)


def _img_attributes(tag: str) -> dict[str, str]:
    """class / alt / id / title of one <img>, lowercased keys."""
    found: dict[str, str] = {}
    for key, value in _IMG_ATTR_RE.findall(tag):
        found.setdefault(key.lower(), value)
    return found


# Words in an <img>'s own class/alt that mark it as furniture rather than
# a photograph.
#
# The URL is not enough evidence. nepalnews.com serves its byline portrait
# from .../wp-content/uploads/2025/08/Nepalnews.jpg - a plain filename with
# an ordinary path and no junk token anywhere in it - and tags it
# class="post-author-image". The markup the outlet ships is the only place
# that says what the picture is, so it is read.
#
# Kept to byline/identity words rather than anything generic: "featured",
# "main" or "large" describe real photographs, and dropping those would
# throw away the article's own image.
_FURNITURE_ATTR_RE = re.compile(
    r"author|avatar|byline|\bwriter|profile|logo|brand|watermark|"
    r"favicon|glyph|bullet",
    re.IGNORECASE,
)


def _is_furniture_img(tag: str) -> bool:
    """True when the <img>'s own class/alt identifies it as furniture."""
    attributes = _img_attributes(tag)
    label = " ".join(
        attributes.get(key, "") for key in ("class", "id", "alt", "title")
    )
    return bool(_FURNITURE_ATTR_RE.search(label))


# A wide, short <img> is an advertising slot, not a photograph. The top of
# this article carried <img width="1200" height="110" src=".../1200-X-110.gif">
# as a leaderboard, and a 10:1 strip is never a news photograph.
_BANNER_MIN_RATIO = 4
_BANNER_MAX_HEIGHT = 200


def _is_banner_img(attributes: dict[str, str]) -> bool:
    """True when declared width/height describe a strip, not a picture."""
    try:
        width = int(attributes.get("width", "0") or 0)
        height = int(attributes.get("height", "0") or 0)
    except ValueError:
        return False
    return bool(width and height and height <= _BANNER_MAX_HEIGHT
                and width >= height * _BANNER_MIN_RATIO)


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


# A resizer that carries the file it is resizing in a query parameter.
# Kathmandu Post serves every body picture through
# assets-api.../thumb.php?src=<the real file>, and weserv.nl through
# ?url=<the real file>.
_RESIZER_PARAM_RE = re.compile(r"[?&](?:src|url)=([^&]+)", re.IGNORECASE)


def unwrap_resizer(url: str | None) -> str | None:
    """
    The real file behind a resizer URL, or None if there is not one.

    A resizer is a proxy: `thumb.php?src=<the real file>` serves a
    downscaled copy of a picture that exists somewhere else, at full
    size, on its own host. Dropping the resizer - which is what the
    candidate list does - throws the photograph away with it.

    So the resizer is unwrapped instead. The real file is not a
    resizer, so it competes on its own merits: it is not penalised for
    being a proxy, and it is the full-size picture rather than the
    300-pixel stand-in.

    The inner value is percent-decoded, because a resizer that is given
    a URL with a query of its own percent-encodes it - and a URL that
    is left encoded points at a file that does not exist.
    """
    if not url:
        return None

    match = _RESIZER_PARAM_RE.search(url)
    if not match:
        return None

    inner = unquote(match.group(1)).strip()
    if not inner.startswith(("http://", "https://")):
        return None

    return inner



def _size_tokens(url: str) -> list[int]:
    """Every number in the URL the site meant as a pixel dimension."""
    found: list[int] = []
    for width_word, dimensions in _SIZE_TOKEN_RE.findall(url):
        token = width_word or dimensions
        try:
            found.append(int(token))
        except ValueError:
            continue
    return found


def _score_image(url: str, declared_width: int | None, declared_height: int | None) -> int:
    """
    Higher is better, but only ever WITHIN a tier.

    Combines any declared dimensions with the size token baked into the
    URL so we prefer e.g. ...-1200x675.jpg over ...-320x180.jpg even when
    only one of them advertises a width. It says nothing about whether an
    image is the story's photograph - see TIER_METADATA above for what
    decides that.
    """
    score = 0
    if declared_width:
        score += min(declared_width, 2000)
    if declared_height:
        score += min(declared_height, 2000) // 2

    tokens = _size_tokens(url)
    if tokens:
        score += min(max(tokens), MAX_SIZE_TOKEN_SCORE)

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



def _is_refused(status: int) -> bool:
    """
    Is this a refusal of the ADDRESS rather than a missing page?

    403, 429 and 451 are the ones these publishers use to turn a
    datacentre address away. 404 is not a refusal: the page is gone,
    and reading it from somewhere else would only find the same 404.
    """
    return status in (401, 403, 405, 406, 407, 429, 451)

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


_SRCSET_ENTRY_RE = re.compile(r"([^\s,]+)\s+([0-9.]+)([wx])")


def _best_srcset_url(value: str) -> str:
    """
    The largest rendition from a srcset, or the value unchanged.

    `_clean_url` takes the FIRST entry of a srcset, which is the smallest
    one - correct for og:image, where we only need an identifier, but
    wrong for an <img>, where the whole point of the srcset is that
    bigger renditions were offered. WordPress emits them in ascending
    order, so the first entry is the one a reader on a phone would get
    and the last is the one worth storing.

    Picks on width descriptors (1200w) when present, otherwise on pixel
    density (2x). Falls back to the first entry, which is what
    _clean_url would have used, when the srcset is unparseable.
    """
    entries = _SRCSET_ENTRY_RE.findall(value)
    if not entries:
        return value.split(",")[0].strip()

    widths = [(float(size), url) for url, size, unit in entries if unit == "w"]
    if widths:
        return max(widths, key=lambda item: item[0])[1]

    densities = [(float(size), url) for url, size, unit in entries if unit == "x"]
    if densities:
        return max(densities, key=lambda item: item[0])[1]

    return entries[0][0]


_ANCHOR_TOKEN_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>|</a\s*>", re.IGNORECASE
)


def _anchor_spans(html: str) -> list[tuple[int, int, str]]:
    """
    (start, end, href) for every <a> in the page, including unclosed ones.

    Nesting is tracked with a stack because a related-story carousel wraps
    each thumbnail in its own link and a malformed page must not pair one
    thumbnail with another story's URL.
    """
    spans: list[tuple[int, int, str]] = []
    stack: list[tuple[int, str]] = []

    for match in _ANCHOR_TOKEN_RE.finditer(html):
        href = match.group(1)
        if href is not None:
            stack.append((match.start(), href))
        elif stack:
            start, opened = stack.pop()
            spans.append((start, match.end(), opened))

    # An anchor left open at the end of the document still wraps whatever
    # follows it.
    spans.extend((start, len(html), href) for start, href in stack)
    return spans


def _is_linked_to_another_story(
    anchors: list[tuple[int, int, str]],
    position: tuple[int, int],
    src: str,
    page_url: str,
) -> bool:
    """
    True when this <img> is a thumbnail inside a link to a DIFFERENT story.

    Every outlet runs a "related / सम्बन्धित समाचार" carousel, and its
    thumbnails are the most persistent wrong-image source there is: they
    are real photographs, they load, and they sit in containers whose
    class names are whatever the theme happened to choose — on
    nepalnews.com a related block is <div class="row"> holding
    <div class="section-padding">, which matches no chrome keyword at all.

    The link is the reliable signal instead. A story's own photograph is
    unlinked, links to itself, or links to a larger copy of itself; a
    related thumbnail links to the other story. Only same-host absolute
    links count: a photo credited to another site is still this story's.
    """
    start, end = position
    host = (urlparse(page_url).hostname or "").lower()

    for anchor_start, anchor_end, href in anchors:
        if not (anchor_start < start and end <= anchor_end):
            continue

        href = href.strip()
        if not href or href.startswith("#"):
            return False
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            return False

        # Relative hrefs are the other half of how WordPress writes a
        # carousel, so they are resolved rather than dismissed.
        absolute = _clean_url(href, page_url)
        if not absolute:
            return False
        if (urlparse(absolute).hostname or "").lower() != host:
            return False  # credited to another site: still this story's photo

        if absolute.split("?")[0].rstrip("/") in (
            src.split("?")[0].rstrip("/"),
            page_url.split("?")[0].rstrip("/"),
        ):
            return False
        if _IMAGE_FILE_RE.search(absolute.split("?")[0]):
            return False  # lightbox to a bigger copy of this same photograph
        return True

    return False


# ---------------------------------------------------------------------------
# Image ranking
# ---------------------------------------------------------------------------

def _rank_image_candidates(
    html: str, meta: dict[str, str], base_url: str
) -> list[tuple[int, int, str, str]]:
    """
    Every image the page offers, best first: (tier, score, url, source).

    Tier decides the order. See TIER_METADATA for why the ranking cannot
    be left to the score.

    Reading order breaks ties inside a tier. A story's photograph is the
    first picture a reader meets on the page, so among equally-plausible
    images the earliest one in the document is the likeliest to be the
    subject. `sorted` is stable, so appending in document order is enough
    to preserve that.
    """
    ranked: list[tuple[int, int, str, str]] = []

    # ---- Tier 1: what the outlet declared this story looks like ----
    #
    # og:image outranks anything found by scanning, because it is the
    # outlet's own statement about this article rather than a guess about
    # its layout. It is skipped when the URL says it is furniture: some
    # outlets set og:image to their channel logo, and then every story
    # renders the same picture.
    for key, fallback_keys, source in (
        ("og:image", ("og:image:secure_url", "og:image:url"), "og:image"),
        ("twitter:image", ("twitter:image:src",), "twitter:image"),
    ):
        url = _clean_url(
            meta.get(key) or next((meta.get(k) for k in fallback_keys if meta.get(k)), None),
            base_url,
        )
        if not url or _looks_like_junk_image(url):
            continue
        width, height = _parse_dimensions(meta, key)
        ranked.append(
            (TIER_METADATA, _score_image(url, width, height), url, source)
        )

    # <link rel="image_src"> is the outlet naming the same picture in a
    # second place, so it shares the metadata tier. apple-touch-icon is
    # deliberately not treated as artwork: it is an app icon by
    # definition, and it is caught as junk by the name.
    link_image = _extract_link_rel(html, ("image_src",), base_url)
    if link_image and not _looks_like_junk_image(link_image):
        ranked.append((TIER_METADATA, _score_image(link_image, None, None), link_image, "link:image_src"))

    # ---- Tiers 2 and 3: images found by scanning the page ----
    #
    # Reading order is the relevance signal here, so within a tier the
    # score only breaks ties. Body images keep CONTENT_IMAGE_BONUS so a
    # genuinely larger in-page rendition still wins between equals.
    region = _article_region_span(html)
    chrome = _chrome_spans(html)
    anchors = _anchor_spans(html)
    seen: set[str] = set()

    for match in _IMG_TAG_RE.finditer(html):
        tag = match.group(0)

        src_match = re.search(
            r'\bsrc(?:set)?\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE
        )
        if not src_match:
            continue

        raw_src = src_match.group(1)
        # A srcset lists renditions smallest first; store the largest.
        if "," in raw_src and re.search(r"\d+\s*[wx]", raw_src):
            raw_src = _best_srcset_url(raw_src)

        src = _clean_url(raw_src, base_url)
        if not src or src in seen:
            continue

        # A byline portrait, a site logo, or a share icon. Checked on the
        # tag's own class/alt because the URL often says nothing.
        if _is_furniture_img(tag):
            continue

        # An advertising slot: wide and short.
        if _is_banner_img(_img_attributes(tag)):
            continue

        # Inside a banner / ad / share / related / byline container. Those
        # are the images that repeat across every story on the site.
        if _inside_any((match.start(), match.end()), chrome):
            continue

        # The thumbnail of a different story, linked from this one. A
        # related-story carousel is real photographs in a container whose
        # class name is whatever the theme chose, so the link is the only
        # dependable evidence that it is furniture.
        if _is_linked_to_another_story(
            anchors, (match.start(), match.end()), src, base_url
        ):
            continue

        if _looks_like_junk_image(src):
            continue

        seen.add(src)
        tier = TIER_ARTICLE_BODY if region and region[0] <= match.start() < region[1] else TIER_PAGE
        # Scored 0 on purpose. Within the in-page tiers the order is
        # reading order, and the size score is not allowed to promote a
        # later image over an earlier one: that is precisely how a
        # related-story thumbnail ending in -1024x556-1.jpg came to
        # outrank the story's own photograph. Size still decides between
        # renditions of the SAME image elsewhere, and still drives
        # upgrade_to_highres.
        ranked.append((tier, 0, src, "body"))

    # Highest tier first; inside a tier, highest score first; and because
    # sorted is stable, document order settles anything still equal.
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_from_article(
    url: str,
    client: httpx.AsyncClient,
    referer: str | None = None,
    max_bytes: int = 3_000_000,
    prefetched_html: str | None = None,
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
        if prefetched_html is not None:
            # The page has already been read - through the reader, when
            # the publisher refuses this address. Parse what was read.
            html = prefetched_html
            final_url = url
        else:
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

            final_url = str(response.url)
            html = ""

        if prefetched_html is None and response.status_code >= 400:
            if not _is_refused(response.status_code):
                logger.debug("media: %s returned %s", url, response.status_code)
                return result
            # REFUSED, NOT GONE.
            #
            # Nine publishers answer this address with a 403 and answer
            # an ordinary client with a 200: the refusal is aimed at
            # the address. The page carries the story's own photograph
            # in its og:image and in its JSON-LD, so reading it from
            # an address that is not refused is the difference between
            # a card with the real picture and a card with key points.
            #
            # The image itself lives on the publisher's CDN, which
            # answers us perfectly well - so the picture can still be
            # proven here, by this worker, once the page is read.
            logger.info(
                "media: %s refused this address (%s); reading it through the "
                "reader.",
                url[:110],
                response.status_code,
            )
            settings = get_settings()
            if not settings.reader_proxy_enabled:
                return result
            html = (
                await fetch_via_reader(
                    url, client, proxy=settings.reader_proxy_url
                )
                or ""
            )
            if not html:
                return result
        elif prefetched_html is None:
            html = response.text

        html = html[:max_bytes]
        meta = _extract_meta_tags(html)

        # ---------------- Image ----------------
        image_candidates = _rank_image_candidates(html, meta, final_url)
        result.declared_images = [
            url for tier, _, url, _ in image_candidates if tier == TIER_METADATA
        ]

        if image_candidates:
            best_tier, best_score, best_url, best_source = image_candidates[0]
            result.image_url = best_url
            result.image_highres_url = best_url
            result.provenance["image_url"] = best_source

            # Keep the whole ranking, deduplicated in tier order, so a
            # rendition that fails validation can be swapped for the next
            # one the same page already offered.
            ranked: list[str] = []
            for _, _, url, _ in image_candidates:
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
        # The page itself, so that a picture request can carry the
        # Referer a browser would send. See ImageValidator.is_live.
        result.source_url = final_url
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

    Resizers are unwrapped rather than merely dropped, and junk is
    dropped outright. A resizer can only return a downscaled version of
    something else, and because proxy URLs are built from a handful of
    shared endpoints the same picture used to land on eight unrelated
    cards - so the proxy itself is never kept. But the photograph
    behind the proxy is the story's own, at full size, on the origin's
    host, and dropping the proxy used to throw that photograph away
    with it. So the proxy is opened and what is inside is kept.

    No image beats a downscaled stand-in: the card has an honest "no
    image available" state and the feed demotes imageless stories.
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
        if not url:
            return
        # The proxy is opened FIRST, because the proxy itself is not a
        # file: `thumb.php` ends in .php, so the "no filename means no
        # file" rule would throw it away - and with it the photograph.
        if _is_resizer(url):
            real = unwrap_resizer(url)
            if not real:
                return
            url = real
        if _looks_like_junk_image(url):
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
      perfectly good images. The page the picture is on travels as the
      Referer, which is what a browser sends; several Nepali CDNs
      refuse a request that claims to come from nowhere, and the
      worker runs from a datacentre in another country, so a probe
      with no Referer is exactly the shape their rule refuses. The
      Referer is the picture's OWN page, never a foreign one.

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

    async def is_live(self, url: str | None, referer: str | None = None) -> bool:
        """
        True only if this URL is proven to return a renderable image.

        `referer` is the page the picture is on. A browser sends it for
        every image it loads; a CDN that checks it refuses a request
        that claims to come from nowhere. The verdict is cached per
        URL, so the referer only matters on the first probe of an
        image.
        """
        if not url:
            return False

        remembered = self._cache.get(url)
        if remembered is not None:
            self._cache.move_to_end(url)
            self.cache_hits += 1
            return remembered

        live = await self._probe(url, referer)
        self._cache[url] = live
        # Bounded: a long-running poll must not accumulate verdicts for
        # every URL it has ever seen. Oldest verdict goes first, which
        # for a single poll is the right thing to forget.
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return live

    async def _probe(self, url: str, referer: str | None = None) -> bool:
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
                    # The page the picture is ON, as the Referer. A
                    # browser sends it for every image it loads, and a
                    # CDN that checks it - several Nepali ones do -
                    # refuses a request that claims to come from
                    # nowhere, or from somewhere else. The worker runs
                    # from a datacentre in another country, and an
                    # image request with no Referer is exactly the
                    # shape a CDN's rule refuses.
                    **({"Referer": referer} if referer else {}),
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
    declared: list[str] | None = None,
    referer: str | None = None,
) -> str | None:
    """
    The first candidate that proves it loads, or None.

    Walks the list in rank order and stops at the first success, so the
    common case costs exactly one request per NEW image. `limit` caps
    the damage when every candidate is dead: a pathological page cannot
    turn one article into an unbounded burst of requests against a CDN
    that is already refusing to serve us.

    `declared` is the page's og:image / twitter:image, passed separately
    so that losing them is visible. Falling back from the outlet's own
    declaration to something scraped out of the page is the exact moment
    a wrong picture gets stored, and it used to pass in silence: the row
    simply carried whatever the scan found, with nothing in the logs to
    say the publisher's choice had been rejected.

    `referer` is the page the picture is on, and it is sent with every
    probe. See ImageValidator.is_live.
    """
    for url in candidates[: max(limit, 0)]:
        if await validator.is_live(url, referer):
            if declared and url not in declared:
                logger.info(
                    "Declared image(s) %s did not load; stored %s found on the page.",
                    ", ".join(declared),
                    url,
                )
            return url

    if declared:
        logger.info(
            "Declared image(s) %s did not load and no other candidate did either.",
            ", ".join(declared),
        )
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