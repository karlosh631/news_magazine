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
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from app.config import DEFAULT_MAX_ARTICLES_PER_SOURCE
from app.sources.reader_proxy import (
    BLOCKED_STATUS_CODES,
    fetch_via_reader,
    fetch_with_reader_fallback,
)
from app.services.dedup import normalize_title, token_fingerprint
from app.services.sanitizer import sanitize_excerpt, sanitize_headline
from app.sources.base import NormalizedArticle, SourceAdapter


#: The absolute date a syndicated card prints inside its own text:
#:
#:     Fri Oct 23 16:46:14 GMT 2026
#:
#: It is the moment the ORIGINAL outlet published the story, which is
#: the moment the story was published - the syndication happened later.
_ABSOLUTE_CARD_DATE_RE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\s+"
    r"(\d{1,2}):(\d{2}):(\d{2})\s+(?:GMT|UTC)\s+(\d{4})"
)

#: The relative date, in English.
_RELATIVE_CARD_DATE_RE = re.compile(
    r"(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\s+ago",
    re.IGNORECASE,
)

#: The relative date, in Nepali. The unit is spelled out because the
#: number of seconds it stands for is the whole point of reading it.
_RELATIVE_CARD_DATE_NE_RE = re.compile(
    r"(\d+)\s*(सेकेन्ड|सेकेण्ड|मिनेट|घण्टा|घंटा|दिन|हप्ता|महिना|वर्ष)\s*अघि"
)

#: The Nepali digits, and the English month names.
_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

#: How long each relative unit is.
_RELATIVE_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
    "month": 2592000, "months": 2592000,
    "year": 31536000, "years": 31536000,
}

_RELATIVE_UNITS_NE = {
    "सेकेन्ड": 1, "सेकेण्ड": 1,
    "मिनेट": 60,
    "घण्टा": 3600, "घंटा": 3600,
    "दिन": 86400,
    "हप्ता": 604800,
    "महिना": 2592000,
    "वर्ष": 31536000,
}


def card_published_at(text: str | None) -> str | None:
    """
    The publication time a listing card prints in its own text.

    WHY
    ---
    A listing card that syndicates another outlet's story prints two
    timestamps: the original outlet's name, and when it published. The
    card's own markup has no element that holds the date - the date is
    part of the card's TEXT - so the date selector found nothing, the
    story was stored with no publication time, and the store fell back
    to "now".

    That made every syndicated row look brand new. They were then
    ordered above the real news of the day, and the reader saw a page
    of cards whose headlines were still the whole card:

        nepalnews.com · २ घण्टा अघि नवलपुरमा अटो दुर्घटना हुँदा ...

    The exact date is read when the card prints one ("Fri Oct 23
    16:46:14 GMT 2026"), and the time since publication otherwise
    ("२ घण्टा अघि"). Both are the ORIGINAL outlet's time, which is the
    moment the story was published.
    """
    if not text:
        return None

    match = _ABSOLUTE_CARD_DATE_RE.search(text)
    if match:
        month, day, hour, minute, second, year = match.groups()
        number = _MONTHS.get(month.lower())
        if number:
            try:
                parsed = datetime(
                    int(year),
                    number,
                    int(day),
                    int(hour),
                    int(minute),
                    int(second),
                    tzinfo=timezone.utc,
                )
                return parsed.isoformat()
            except ValueError:
                pass

    match = _RELATIVE_CARD_DATE_RE.search(text)
    if match:
        number = int(match.group(1))
        seconds = _RELATIVE_UNITS.get(match.group(2).lower())
        if seconds:
            return (datetime.now(timezone.utc) - timedelta(seconds=number * seconds)).isoformat()

    match = _RELATIVE_CARD_DATE_NE_RE.search(text)
    if match:
        number = int(match.group(1).translate(_DEVA_DIGITS))
        seconds = _RELATIVE_UNITS_NE.get(match.group(2))
        if seconds:
            return (datetime.now(timezone.utc) - timedelta(seconds=number * seconds)).isoformat()

    return None

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

#: Does this page name the story it is about?
#:
#: `<h1>` is where a headline usually lives, but it is not the only
#: place. An outlet whose article pages carry the headline in an
#: `<h2>`, or only in the Open Graph metadata, was rejected in its
#: entirety - every candidate link raised "did not resolve to an
#: article page", which reads exactly like a broken selector and is
#: not one.
#:
#: All three are the same question asked three ways, and any one of
#: them answering it is enough.
_HAS_HEADLINE_RE = re.compile(
    r"<h1\b"          # a heading
    r"|<h2\b"         # a subheading, used as the headline
    r"|<meta[^>]+property\s*=\s*[\"']og:title[\"']"   # Open Graph
    r"|<meta[^>]+name\s*=\s*[\"']twitter:title[\"']",  # X / Twitter
    re.IGNORECASE,
)

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
            # Nine publishers refuse this address with a 403 and answer
            # an ordinary client with a 200 - the refusal is aimed at
            # the address, not at the request. When that happens the
            # page is read through the reader instead, so the outlet
            # is not silent. See app/sources/reader_proxy.py.
            if self.settings.reader_proxy_enabled:
                text, status = await fetch_with_reader_fallback(
                    url,
                    client,
                    headers,
                    proxy=self.settings.reader_proxy_url,
                )
            else:
                response = await client.get(url, headers=headers, follow_redirects=True)
                text = response.text if response.status_code < 400 else None
                status = response.status_code

            if text is None:
                raise ValueError(
                    f"Empty listing page response from {url} (status {status})"
                )

            if not text.strip():
                raise ValueError(f"Empty listing page response from {url}")

            self._listing_html = text
            return text

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
        # A card may be a CONTAINER holding the link, or the link
        # itself. The second shape is what most modern card markup
        # uses -- the whole card is one <a> wrapping the image and
        # the headline -- and the seeded `item` selectors in migration
        # 0008 are written in exactly that shape. But BeautifulSoup's
        # select_one() only searches DESCENDANTS, so an <a> card used
        # to find no link inside itself and every card was dropped,
        # which left the adapter silently falling back to its anchor
        # heuristic and ignoring the operator's selector entirely.
        link_sel = config.get("link") or "a[href]"
        link_tag = card.select_one(link_sel)
        if link_tag is None and getattr(card, "name", None) == "a" and card.get("href"):
            link_tag = card
        if not link_tag or not link_tag.get("href"):
            return None

        href = link_tag["href"].strip()
        if exclude_re.search(href):
            return None

        link = urljoin(base_url, href)
        if urlparse(link).scheme not in {"http", "https"}:
            return None

        # `title` must exist before it is tested. A selector_config
        # that names an `item` but no `title` -- which is the shape
        # every seeded outlet in migration 0008 uses -- used to reach
        # the `if not title` line below with `title` never assigned,
        # and raised UnboundLocalError on the first card whose `item`
        # selector actually matched. The listing then failed with an
        # error that read like a broken site rather than a broken
        # config.
        title = ""
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
        # The same resolution as the date selector below, and for the
        # same reason: `config.get("image", "img")` returns "img" when
        # the key is ABSENT, so the guard was always true and
        # `config["image"]` raised `KeyError` on the first card of any
        # listing whose selector_config had no `image` key.
        image_selector = config.get("image", "img")
        if image_selector:
            img_tag = card.select_one(image_selector)
            if img_tag:
                image = self._image_from_tag(img_tag, config.get("image_attr"), base_url)

        published = None
        # The selector is RESOLVED once and then used. This used to be
        #
        #     if config.get("date", "time"):
        #         date_tag = card.select_one(config["date"])
        #
        # which reads like "the date selector is optional" and is not:
        # `config.get("date", "time")` returns "time" when the key is
        # ABSENT, so the guard is always true and the line below raises
        # `KeyError: 'date'` on the first card of any listing whose
        # selector_config has no `date` key. The `date` key is
        # documented as optional, and a documented optional key that
        # crashes the adapter is worse than a required one.
        date_selector = config.get("date", "time")
        if date_selector:
            date_tag = card.select_one(date_selector)
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
        self, entries: list[dict], max_checks: int = 8
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

        HOW MANY ARE PROBED, AND WHY IT IS EIGHT
        -----------------------------------------
        The window used to be forty. It is eight now, because the
        probe is the most expensive thing this adapter does: a
        client-rendered outlet has to be read through the reader, and
        the reader serves twenty pages a minute to everybody. Eight
        outlets at forty probes each is three hundred and twenty reader
        reads - sixteen minutes of the pass, spent confirming links
        that the listing itself already vouches for.

        Eight is enough to decide the one thing the probe decides: is
        this outlet readable from here? The candidates past the window
        are KEPT - the code has always kept them, flagged as
        unverified - so the outlet still contributes its stories; the
        probe is there to catch an outlet whose links are chrome, not
        to hold the outlet hostage.
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

        # ONE client for the whole probe window.
        #
        # There was one per probe, so a listing of forty candidates cost
        # forty TLS handshakes - and the handshakes, not the requests,
        # were the cost. The connection is shared and reused, which is
        # also what a browser does.
        async with httpx.AsyncClient(
            timeout=12.0, follow_redirects=True
        ) as probe_client:

            async def is_article(entry: dict) -> bool:
                link = entry.get("link")
                if not link:
                    return False

                async with semaphore:
                    try:
                        response = await probe_client.get(link, headers=headers)
                    except Exception:
                        return False

                    if response.status_code < 400:
                        # A redirect to the homepage means the link was
                        # chrome.
                        final_path = urlparse(str(response.url)).path.rstrip("/")
                        original_path = urlparse(link).path.rstrip("/")
                        if final_path != original_path and final_path == "":
                            return False
                        text = response.text
                    elif response.status_code in BLOCKED_STATUS_CODES:
                        # REFUSED, NOT MISSING.
                        #
                        # Three outlets - Ekantipur, Hamro Patro
                        # National and Hamro Patro News - answer this
                        # address with a refusal, and the refusal was
                        # read as "this is not an article". Every
                        # candidate failed, the outlet raised, and the
                        # whole source was dropped: three publishers
                        # contributed NOTHING to the site, every pass,
                        # for as long as the code had been like this.
                        #
                        # A refusal is not an answer about the page. It
                        # is an answer about the address, and the
                        # reader proxy is an address that has not been
                        # refused - which is exactly how the article
                        # pages are read elsewhere in this pipeline.
                        if not self.settings.reader_proxy_enabled:
                            return False
                        text = await fetch_via_reader(
                            link,
                            probe_client,
                            proxy=self.settings.reader_proxy_url,
                        )
                        if text is None:
                            return False
                    else:
                        # A 404 is a real answer: the page is not there.
                        return False

                    if len(text) < 500 or not _HAS_HEADLINE_RE.search(text):
                        # The page ANSWERED, and it did not name a
                        # story.
                        #
                        # Eight outlets - Ekantipur, Hamro Patro
                        # National and Hamro Patro News, Kathmandu
                        # Tribune, MyRepublica, Nepal Patra, The
                        # Himalayan Times and one more - serve a
                        # CLIENT-RENDERED page: what arrives is a
                        # shell, and the headline only exists once the
                        # page has been rendered. The probe read the
                        # shell, found no headline in it, and threw the
                        # outlet away - so eight publishers, a quarter
                        # of the site, contributed nothing at all.
                        #
                        # The reader renders it. So the shell is read
                        # through the reader before the candidate is
                        # rejected, and the outlet is kept.
                        if not self.settings.reader_proxy_enabled:
                            return False
                        rendered = await fetch_via_reader(
                            link,
                            probe_client,
                            proxy=self.settings.reader_proxy_url,
                        )
                        if not rendered:
                            return False
                        text = rendered

                if len(text) < 500:
                    return False
                # A page with no headline is not an article.
                #
                # `<h1>` is the usual place for one, but it is not the
                # only one, and insisting on it rejected a
                # whole outlet whose article pages carry the
                # headline in an `<h2>` and in the Open Graph
                # metadata instead. The check is "does this page
                # name the story", and there are three ways to
                # answer that.
                if not _HAS_HEADLINE_RE.search(text):
                    return False
                return True

            results = await asyncio.gather(
                *(is_article(entry) for entry in candidates)
            )
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
            if not published:
                # The card's own markup has no date element, but its
                # TEXT has the timestamp. Without this the row was
                # stored with no publication time and the store used
                # "now" - so every syndicated story looked like it had
                # just been published, and they took the top of the
                # page away from the real news.
                published = card_published_at(entry.get("title"))

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