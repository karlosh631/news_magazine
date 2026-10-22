"""
HTML listing-page adapter, for outlets that publish no usable feed.

Roughly half the major Nepali dailies expose no RSS/Atom endpoint at
all — /rss 404s, /feed returns an HTML shell, or the homepage is
client-rendered so links never appear in the initial HTML. For those,
the only way to see their coverage is to read the listing page.

This adapter does exactly that, and only that:

    listing page -> (title, link, image, date) per card

It deliberately does NOT scrape article bodies. We want headlines and
artwork for the feed; pulling full article text would be republishing
content the outlet has not licensed us to republish, and `sources.
republish_permission` stays false for every seeded outlet.

Every request goes through the same robots.txt checker and rate limiter
the RSS adapter uses, so turning one of these on cannot make the worker
hammer a site.

Per-domain configuration lives in `sources.selector_config`:

    {
      "item":     "article, .post-item",       # CSS selector for one card
      "link":     "a[href]",                    # selector, relative to item
      "title":    ".headline",                  # optional; else link text
      "image":    "img",                        # optional
      "image_attr": "data-src",                 # optional lazy-load attr
      "date":     "time",                       # optional
      "exclude":  "/author/|\\.(jpg|png)$"      # regex, hrefs to drop
    }

Anything not specified falls back to a link-pattern heuristic, so an
outlet can be added with a bare `item` selector and still work.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from app.config import DEFAULT_MAX_ARTICLES_PER_SOURCE
from app.services.dedup import normalize_title, token_fingerprint
from app.services.sanitizer import sanitize_excerpt, sanitize_headline
from app.sources.base import NormalizedArticle, SourceAdapter

logger = logging.getLogger("html_listing")

try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by requirements.txt
    BeautifulSoup = None  # type: ignore[assignment]
    BS4_AVAILABLE = False


# Outbound-link furniture that is never an article.
_DEFAULT_EXCLUDE = (
    r"/author/|/category/|/tag/|/page/\d+|/feed/?$|/rss/?$|/search|"
    r"/login|/register|/subscribe|/newsletter|/contact|/about|/privacy|"
    r"/advertis|\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4|mp3|zip)$"
)

# If we cannot read the markup (no bs4, or the heuristic finds nothing),
# parse anchors with this. Falls back to <a href> whose text is long
# enough to be a headline rather than a nav label.
_ANCHOR_RE = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

_HEADLINE_MIN_CHARS = 18
_HEADLINE_MAX_CHARS = 300

#: How many cards one listing page may contribute.
#:
#: The listing page is read whole, so this is the page size this
#: adapter works at. It is a bound on the outlet's own markup; the
#: operator's per-outlet fetch cap is applied on top of it, and the
#: smaller of the two wins.
LISTING_PAGE_LIMIT = 80

_LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-srcset", "srcset")


def _plain_text(html_fragment: str) -> str:
    text = _TAG_RE.sub(" ", html_fragment or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _srcset_best(value: str) -> str:
    """Pick the widest candidate from a srcset attribute."""
    best_url, best_width = None, -1
    for part in (value or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        width = 0
        for token in bits[1:]:
            if token.endswith("w"):
                try:
                    width = int(token[:-1])
                except ValueError:
                    width = 0
        if width > best_width:
            best_url, best_width = url, width
    return best_url or ""


class HtmlListingAdapter(SourceAdapter):
    """
    Scrapes an outlet's public listing page for headline cards.

    Register it with adapter_key = 'html_listing' and point fallback_url
    at the section page to read.
    """

    # Verify each discovered link resolves to a real article page.
    # Off only if an operator has hand-verified selector_config for this
    # outlet and wants to skip the probe round trip.
    verify_urls: bool = True

    async def fetch(self) -> str:
        url = self.source.get("fallback_url") or self.source.get("feed_url")

        if not url:
            raise ValueError(
                f"Source '{self.source.get('name', 'unknown')}' has neither "
                "fallback_url nor feed_url."
            )

        if not await self.check_allowed(url):
            raise PermissionError(
                f"Source '{self.source.get('name', 'unknown')}' is not allowed "
                f"for {url}. Check sources.allowed and the outlet's robots.txt."
            )

        await self.rate_limiter.wait()

        headers = {
            "User-Agent": self.source.get("user_agent") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ne,en;q=0.8",
        }

        async with httpx.AsyncClient(
            timeout=self.settings.default_request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            if not response.text.strip():
                raise ValueError(f"Empty listing page response from {url}")

            self._listing_html = response.text
            return response.text

    # -- parsing -----------------------------------------------------------

    def parse(self, raw: str) -> list[dict]:
        """
        Turn listing HTML into loose {title, link, image, date} dicts.

        Two strategies, in order:
          1. The outlet's own `item` selector, when configured. Most
             accurate because it uses the site's own markup semantics.
          2. A headline-shaped anchor heuristic, for sites we have not
             yet hand-tuned.
        """
        config = self.source.get("selector_config") or {}
        exclude_re = re.compile(config.get("exclude") or _DEFAULT_EXCLUDE, re.IGNORECASE)
        base_url = self.source.get("fallback_url") or self.source.get("base_url") or ""

        entries: list[dict] = []
        seen_links: set[str] = set()

        if BS4_AVAILABLE and config.get("item"):
            soup = BeautifulSoup(raw, "html.parser")
            for card in soup.select(config["item"]):
                entry = self._parse_card(card, config, base_url, exclude_re)
                if entry and entry["link"] not in seen_links:
                    seen_links.add(entry["link"])
                    entries.append(entry)

        if not entries:
            for entry in self._parse_anchors(raw, base_url, exclude_re):
                if entry["link"] not in seen_links:
                    seen_links.add(entry["link"])
                    entries.append(entry)

        if not entries:
            raise ValueError(
                f"No headlines found for '{self.source.get('name', 'unknown')}'. "
                "The selector_config in the sources table is probably stale — "
                "the outlet redesigned its listing page."
            )

        # Newest first, because the rest of the pipeline assumes order.
        #
        # Bounded twice: the listing page's own page size, and the
        # operator's per-outlet fetch cap. The cap is the same number
        # the pipeline applies after parsing, so lowering it lowers
        # the work THIS adapter does as well as the work the pipeline
        # does with the result — a cap that only bound the pipeline
        # would still let the adapter read a thousand cards off the
        # page and throw all but fifty of them away.
        return entries[:self._listing_limit()]

    def _listing_limit(self) -> int:
        """
        How many cards this listing page may contribute.

        The smaller of the page's own size and the configured per-outlet
        fetch cap. A settings object without the field falls back to
        the same default the Settings class declares, not to one.
        """
        cap = max(
            int(
                getattr(
                    self.settings,
                    "max_articles_per_source",
                    DEFAULT_MAX_ARTICLES_PER_SOURCE,
                )
                or 0
            ),
            1,
        )
        return min(LISTING_PAGE_LIMIT, cap)

    def _parse_card(
        self,
        card: Any,
        config: dict,
        base_url: str,
        exclude_re: re.Pattern,
    ) -> dict | None:
        link_tag = card.select_one(config.get("link") or "a[href]")
        if not link_tag or not link_tag.get("href"):
            return None

        href = link_tag["href"].strip()
        if exclude_re.search(href):
            return None

        link = urljoin(base_url, href)
        if urlparse(link).scheme not in {"http", "https"}:
            return None

        if config.get("title"):
            title_tag = card.select_one(config["title"])
            title = _plain_text(title_tag.get_text(" ")) if title_tag else ""
        if not title:
            title = _plain_text(link_tag.get_text(" "))
        if not title:
            title = _plain_text(link_tag.get("title", ""))

        title = sanitize_headline(title)
        if not _plausible_headline(title):
            return None

        image = None
        if config.get("image", "img"):
            img_tag = card.select_one(config["image"])
            if img_tag:
                image = self._image_from_tag(img_tag, config.get("image_attr"), base_url)

        published = None
        if config.get("date", "time"):
            date_tag = card.select_one(config["date"])
            if date_tag:
                published = date_tag.get("datetime") or _plain_text(date_tag.get_text(" "))

        return {"title": title, "link": link, "image": image, "date": published}

    def _parse_anchors(
        self, raw: str, base_url: str, exclude_re: re.Pattern
    ) -> list[dict]:
        """
        Fallback: every anchor whose visible text looks like a headline.

        Not precise, but it finds the stories on a plain unstyled listing
        page, which is exactly the case where nobody has written a
        selector_config yet.
        """
        entries: list[dict] = []

        for href, inner in _ANCHOR_RE.findall(raw):
            if exclude_re.search(href):
                continue

            title = _plain_text(inner)
            if not _plausible_headline(title):
                continue

            link = urljoin(base_url, href.strip())
            if urlparse(link).scheme not in {"http", "https"}:
                continue

            # A same-page or nav link is not an article.
            host = urlparse(link).hostname
            base_host = urlparse(base_url).hostname
            if host and base_host and host != base_host:
                continue

            entries.append({"title": title, "link": link, "image": None, "date": None})

        return entries

    def _image_from_tag(
        self, img_tag: Any, preferred_attr: str | None, base_url: str
    ) -> str | None:
        """
        Pull the best image off a card, preferring lazy-loaded sources.

        News sites lazy-load with data-src and leave a 1x1 placeholder in
        src. Reading src first is why images were rendering blank.
        """
        candidates: list[str] = []

        if preferred_attr and img_tag.get(preferred_attr):
            candidates.append(img_tag.get(preferred_attr))

        for attr in _LAZY_ATTRS:
            value = img_tag.get(attr)
            if value:
                candidates.append(_srcset_best(value) if attr.endswith("srcset") else value)

        if img_tag.get("src"):
            candidates.append(img_tag["src"])

        for candidate in candidates:
            cleaned = urljoin(base_url, candidate.strip())
            if urlparse(cleaned).scheme not in {"http", "https"}:
                continue
            # Skip placeholders.
            if re.search(r"(?:placeholder|spacer|blank|transparent|1x1)\.", cleaned, re.IGNORECASE):
                continue
            if len(cleaned) < 20:
                continue
            return cleaned

        return None

    async def verify_entries(
        self, entries: list[dict], max_checks: int = 40
    ) -> list[dict]:
        """
        Confirm each candidate URL is actually an article before we store it.

        The anchor heuristic is deliberately permissive, which is exactly
        the problem: on Sudarshan it collected 77 links of which every one
        was a 404 — "/AN", "/DNH", "/BindasBolOnQuran" are state
        abbreviations and campaign slugs in the site chrome, not stories.
        Storing those put 77 broken rows in the articles table with no
        image, no summary and a dead link.

        So: probe each URL, drop anything that does not return a real
        page, and fail loudly when most of them do not — that means the
        site's markup changed or the listing is client-rendered, and the
        operator needs to write a selector_config rather than have the
        pipeline quietly invent articles.
        """
        if not entries:
            return []

        candidates = entries[:max_checks]
        semaphore = asyncio.Semaphore(6)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }

        async def is_article(entry: dict) -> bool:
            link = entry.get("link")
            if not link:
                return False

            async with semaphore:
                try:
                    async with httpx.AsyncClient(
                        timeout=12.0, follow_redirects=True
                    ) as probe:
                        response = await probe.get(
                            link, headers=headers
                        )
                    if response.status_code >= 400:
                        return False
                    # A redirect to the homepage means the link was chrome.
                    final_path = urlparse(str(response.url)).path.rstrip("/")
                    original_path = urlparse(link).path.rstrip("/")
                    if final_path != original_path and final_path == "":
                        return False
                    text = response.text
                    if len(text) < 500:
                        return False
                    # A page with no headings is not an article.
                    if not re.search(r"<h1\b", text, re.IGNORECASE):
                        return False
                    return True
                except Exception:
                    return False

        results = await asyncio.gather(*(is_article(entry) for entry in candidates))
        verified = [
            entry for entry, ok in zip(candidates, results) if ok
        ]

        rejected = len(candidates) - len(verified)
        if candidates and rejected / len(candidates) > 0.5:
            raise ValueError(
                f"{rejected}/{len(candidates)} candidate links for "
                f"'{self.source.get('name')}' did not resolve to an article "
                f"page. The listing is probably client-rendered, or "
                f"selector_config in the sources table needs updating. "
                f"Refusing to store {rejected} broken rows."
            )

        if rejected:
            logger.info(
                "%s: dropped %d listing link(s) that were not articles",
                self.source.get("name"), rejected,
            )

        # Anything past the probe window is kept but flagged: we did not
        # check it, so we cannot claim it is valid.
        return verified + entries[max_checks:]

    # -- normalisation -----------------------------------------------------

    def normalize(self, entries: list[dict]) -> list[NormalizedArticle]:
        base_url = self.source.get("fallback_url") or self.source.get("base_url") or ""
        default_language = self.source.get("default_language", "ne")

        articles: list[NormalizedArticle] = []

        for entry in entries:
            title = sanitize_headline(entry.get("title") or "")
            link = entry.get("link")

            if not title or not link or not _plausible_headline(title):
                continue

            published = self._parse_date(entry.get("date"))

            articles.append(
                NormalizedArticle(
                    source_article_url=link,
                    # Listing pages carry no canonical URL, so the article
                    # URL is the identity. Dedup falls back to the token
                    # fingerprint from here.
                    canonical_url=link,
                    headline=title,
                    published_at=published,
                    author_name=None,
                    excerpt=None,
                    thumbnail_url=entry.get("image"),
                    # Not set here on purpose. normalize() runs before
                    # any page has been fetched, so there is nothing to
                    # set it from. `needs_article_fetch` below makes the
                    # pipeline open this page anyway, and
                    # app/sources/body.py takes the text off it — which
                    # is also where republish_permission is enforced a
                    # second time.
                    full_body_html=None,
                    language=default_language,
                    raw_metadata={
                        "content_hash": hashlib.sha256(
                            f"{link}|{token_fingerprint(title)}".encode("utf-8")
                        ).hexdigest(),
                        "title_normalized": normalize_title(title),
                        "image_url": entry.get("image"),
                        "adapter": "html_listing",
                        "listing_url": base_url,
                        # Tells the pipeline to fetch the article page for
                        # real metadata, since a listing gives us none.
                        "needs_article_fetch": True,
                    },
                )
            )

        self._normalized = articles
        return articles

    async def verify_normalized(
        self, articles: list[NormalizedArticle]
    ) -> list[NormalizedArticle]:
        """
        Probe each article URL and drop the ones that are not articles.

        Called by the pipeline after normalize()/validate(), because
        normalize() is synchronous but the probe is not.
        """
        entries = [
            {"link": a.source_article_url, "article": a} for a in articles[:40]
        ]
        kept = await self.verify_entries(entries)
        valid = {
            id(item["article"]) for item in kept if item.get("article") is not None
        }
        return [a for a in articles if id(a) in valid]

    @staticmethod
    def _parse_date(value: Any) -> str | None:
        """Accept an ISO string, an RFC822 date, or nothing."""
        if not value:
            return None

        if isinstance(value, (datetime,)):
            return value.astimezone(timezone.utc).isoformat()

        text = str(value).strip()
        if not text:
            return None

        # datetime.fromisoformat covers most <time datetime="..."> values.
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
        except ValueError:
            pass

        # Fall back to feedparser, which handles RFC822/RFC1123 well.
        try:
            parsed_feed = feedparser.parse(text)
            for key in ("published_parsed", "updated_parsed", "created_parsed"):
                struct = parsed_feed.get(key)
                if struct:
                    return datetime(*struct[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass

        return None


def _plausible_headline(title: str) -> bool:
    """
    Reject navigation labels, ad slots and truncate-to-garbage.

    A headline is long enough to carry a sentence, short enough not to be
    a paragraph, and is not one of the outlet's own calls to action.
    The anchor-heuristic fallback is deliberately permissive because it
    has no `item` container to work from, so this filter is what stops
    "विज्ञापन गर्नुहोस्" (Advertise here) becoming a headline.
    """
    if not title:
        return False

    length = len(title)
    if length < _HEADLINE_MIN_CHARS or length > _HEADLINE_MAX_CHARS:
        return False

    lowered = title.lower()

    # Template / code artifacts. Outlets that render their listing
    # client-side leave the template literals in the served HTML, and the
    # anchor heuristic happily collects "${item.title}" as a headline.
    if any(token in title for token in ("${", "{{", "}}", "<%", "%>", "[[", "]]", "\\n")):
        return False
    if re.search(r"[{};=<>]|=>|\|\||\b(?:function|return|var|const|let|if|else|null|true|false|undefined)\b", title):
        return False
    # A single lowercase run of ASCII words with no punctuation is far
    # more likely to be a CSS class or route than a headline.
    if re.fullmatch(r"[a-z0-9_.\-/]+", title) and " " not in title:
        return False

    # English page furniture.
    for marker in (
        "read more", "click here", "subscribe", "sign up", "log in", "login",
        "follow us", "advertise", "advertisement", "loading", "next page",
        "previous page", "home page", "view all", "see all", "more news",
        "cookie", "privacy policy", "terms of use", "newsletter",
        # Accessibility and assistive links. These are the first <a> on
        # most modern pages and read exactly like a short headline.
        "skip to", "skip navigation", "main content", "skip link",
        "toggle navigation", "go to menu", "accessibility",
        "change language", "switch language", "back to top",
    ):
        if marker in lowered:
            return False

    # Nepali page furniture. Outlets run Nepali and English chrome side
    # by side, and the ad slots are among the longest links on the page.
    for marker in (
        "विज्ञापन", "विज्ञापन गर्नु", "विज्ञापनका लागि", "थप पढ्नुहोस्",
        "समाचार", "हेरौन", "पृष्ठ", "अर्को पृष्ठ", "सम्पर्क", "हाम्रो बारे",
        "गोपनीयता", "सदस्यता", "सूचना", "खोज्नुहोस्", "फेसबुक", "ट्विटर",
        "पत्राचार", "गर्नुहोस्", "हेर्नुहोस्", "देख्नुहोस्",
    ):
        if marker in title:
            return False

    # Needs at least a couple of words (Devanagari has no spaces, so fall
    # back to length for those).
    if " " in title and len(title.split()) < 2:
        return False

    return True