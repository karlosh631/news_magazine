"""
Full article body extraction — the PRODUCER for `full_body_html`.

`sources.republish_permission` (migration 0001: "full-text republish
allowed?") is the operator's per-outlet licence decision.
`app/sources/base.py:127` enforces it by nulling `full_body_html` unless
the flag is set, and `app/services/ingestion.py` reads it back through
`_build_body()`. But for as long as the gate existed, NOTHING ever
populated the field: both adapters hard-set `full_body_html=None` at
`normalize()` time, which is before any page has been fetched. The gate
was guarding a value nobody produced, so flipping the flag in the
database changed nothing and the article page's "read more" expanded
three sentences of excerpt and stopped.

This module is the producer, and it is the narrowest one that works.

Design
------

**Zero extra requests in the common case.** Media enrichment already
fetches the article page for og:image / video / audio, and that page
already yielded `ExtractedMedia.article_text` via trafilatura — used
only to feed the extractive summariser. `attach_full_body()` takes
that already-parsed result and does nothing else. No second request, no
second parse, no new crawl surface.

**The extra request is a separate, second opt-in.** Some articles never
trigger a media fetch (the feed supplied an image and the adapter is
not a listing scanner), so they would get no body. Covering them means
one page request per story into 27 publishers — that is a crawl, not a
poll, and it is exactly the behaviour that gets an operator's IP
blocked. It therefore needs `fetch_full_text` on top of `enrich_body`,
and both default to False. `enrich_body` alone is free.

**Fail closed, twice.** The permission check is repeated here even
though `base.validate()` already enforces it, because
`attach_full_body()` runs per-article, AFTER `validate()` has already
had its say, so the base gate never sees the value written here. The
flag is consulted before anything costs a request AND again before the
text is stored: no permission means no fetch, and a page that happens
to be in hand is not stored either.

**Plain text, never the publisher's markup.** Full-text republication is
a licensing decision and carrying a publisher's HTML to a reader is a
security one. They are separable, and `_build_body()` rebuilds escaped
`<p>` blocks from plain text, so the markup would buy nothing while
adding the entire XSS surface to a database column.

**Bounded.** `MAX_BODY_LENGTH` is the same constant `_build_body()`
applies, so what is stored is exactly what will be rendered.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from app.services.sanitizer import sanitize_plain_text

from .media import ExtractedMedia, extract_from_article

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .base import RateLimiter, RobotsChecker

logger = logging.getLogger("body")


# A full article body. The SAME number services/ingestion.py's
# _build_body() applies, imported rather than re-declared, so what is
# stored is exactly what will be rendered and the two can never drift.
MAX_BODY_LENGTH = 20_000

# Below this many characters a page is a stub, a cookie banner or a JS
# shell rather than an article. Matches media.MIN_ARTICLE_TEXT_CHARS,
# and is a second line of defence behind it: a page whose body
# sanitises down to a nav menu is not worth publishing.
MIN_BODY_CHARS = 120

# Blank line between paragraphs. trafilatura emits "\n\n" between the
# prose blocks it recovers, which is the conventional plain-text
# paragraph separator and the only structure a markup-free body can
# still carry.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def permits_full_text(source_row: dict | None) -> bool:
    """
    The operator's per-outlet licence decision.

    Missing key means False. A source row that lost the column, or a
    caller that passed the wrong dict, must read as "not licensed"
    rather than as "probably fine".
    """
    return bool((source_row or {}).get("republish_permission", False))


def to_plain_paragraphs(
    value: str | None,
    max_length: int = MAX_BODY_LENGTH,
) -> str | None:
    """
    Publisher markup -> bounded PLAIN TEXT, paragraphs separated by a blank line.

    Split on blank lines FIRST, sanitise each block on its own, then
    rejoin. Sanitising the whole document in one call is what welds an
    article into a single run-on line: `_strip_html()` collapses every
    run of whitespace to a single space, so paragraph breaks are gone
    before anything else looks at them. Splitting first keeps them.

    Sanitising per block rather than by hand-rolled regex is
    deliberate — this reuses the audited strip in services/sanitizer.py
    (script/style removal, comment removal, tag stripping, entity
    decoding) instead of growing a second, weaker one.
    """
    if not value:
        return None

    paragraphs: list[str] = []
    budget = max(max_length, 0)

    for block in _PARAGRAPH_SPLIT.split(str(value)):
        if budget <= 0:
            break
        clean = sanitize_plain_text(block, budget).strip()
        if not clean:
            continue
        budget -= len(clean) + 2  # the "\n\n" this block will be rejoined with
        paragraphs.append(clean)

    if not paragraphs:
        return None

    # Hard slice as well as the running budget: a pathological page
    # must not write a megabyte per article across 27 outlets.
    return "\n\n".join(paragraphs)[:max_length].strip() or None


def body_text_from_extracted(
    extracted: ExtractedMedia | None,
    max_length: int = MAX_BODY_LENGTH,
) -> str | None:
    """
    The licensed plain-text body of a page that has ALREADY been fetched.

    None when there was no fetch, or when the recovered text is too
    short to be an article. Never raises.
    """
    if extracted is None:
        return None

    try:
        raw = getattr(extracted, "article_text", None)
    except Exception:  # noqa: BLE001 — a property raising is still not fatal
        return None

    text = to_plain_paragraphs(raw, max_length)
    if not text or len(text) < MIN_BODY_CHARS:
        return None
    return text


def _may_fetch_its_own_page(settings: Any) -> bool:
    """
    True only when the operator asked for the extra request.

    `enrich_body` on its own is free; `fetch_full_text` is the knob
    that spends a request per article, and it is a separate flag
    precisely so nobody turns body storage on by accident and acquires a
    per-story crawl into 27 publishers.
    """
    return bool(getattr(settings, "fetch_full_text", False))


async def _fetch_article_page(
    article: Any,
    settings: Any,
    client: httpx.AsyncClient | None,
    robots: "RobotsChecker | None",
    rate_limiter: "RateLimiter | None",
) -> ExtractedMedia | None:
    """
    The one page request this module is allowed to make on its own account.

    Honours robots.txt and the adapter's per-host rate limiter before
    touching the network. The robots check uses the honest bot identity
    from settings rather than the browser User-Agent media.py sends,
    because the identity checked against robots.txt has to be the one
    the operator registered.

    Never raises: a failed body fetch is an article with an excerpt, not
    a sync that dies.
    """
    url = getattr(article, "source_article_url", None)
    if not url:
        return None

    if robots is not None:
        try:
            user_agent = getattr(settings, "default_user_agent", "") or ""
            if not await robots.is_allowed(url, user_agent):
                logger.info(
                    "body: robots.txt disallows %s; storing excerpt only.", url
                )
                return None
        except Exception as exc:  # noqa: BLE001 — unknown must read as "no"
            logger.debug("body: robots check failed for %s: %s", url, exc)
            return None

    if rate_limiter is not None:
        try:
            await rate_limiter.wait()
        except Exception:  # noqa: BLE001
            logger.debug("body: rate limiter failed for %s", url)
            return None

    # Reuse the run's pooled client when there is one; only build a
    # throwaway client when this module is called standalone.
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=float(getattr(settings, "default_request_timeout_seconds", 15.0)),
        follow_redirects=True,
    )

    try:
        referer = (getattr(article, "raw_metadata", None) or {}).get("listing_url")
        return await extract_from_article(url, http, referer=referer)
    except Exception as exc:  # noqa: BLE001 — extraction must never break a sync
        logger.debug("body: page fetch failed for %s: %s", url, exc)
        return None
    finally:
        if own_client:
            await http.aclose()


async def attach_full_body(
    article: Any,
    source_row: dict,
    settings: Any,
    *,
    extracted: ExtractedMedia | None = None,
    client: httpx.AsyncClient | None = None,
    robots: "RobotsChecker | None" = None,
    rate_limiter: "RateLimiter | None" = None,
) -> bool:
    """
    Populate `article.full_body_html` from the article's own page.

    Returns True when the field was set. Everything else — setting off,
    licence refused, nothing to extract, page unreachable — returns
    False with `full_body_html` left exactly as it was, which for a
    licensed-but-unreachable story means the excerpt is what gets
    stored. That is the correct outcome: a body we could not fetch is
    not a licence to publish something else.

    Args:
        article: The NormalizedArticle being processed. Mutated in place,
                because `_build_body()` reads the field off it.
        source_row: The sources row. Supplies `republish_permission`.
        settings: Supplies `enrich_body` / `fetch_full_text`.
        extracted: An ExtractedMedia from a page ALREADY fetched this
                run. Reused verbatim — this is the zero-extra-request
                path and the one that matters.
        client: The run's pooled httpx client, for the rare own-fetch.
        robots / rate_limiter: The adapter's politeness plumbing, used
                before that own-fetch. Neither is consulted when the
                page was already fetched, because that fetch already
                happened under the pipeline's own rules.

    Never raises.
    """
    # 1. The opt-in. When off this function touches neither the
    #    article nor the network.
    if not getattr(settings, "enrich_body", False):
        return False

    # 2. The licence, checked before anything that costs a request.
    if not permits_full_text(source_row):
        return False

    if extracted is not None:
        # The page is already in hand. No robots check, no rate-limit
        # wait, no second request — it was fetched a moment ago under
        # the pipeline's own rules.
        text = body_text_from_extracted(extracted)
    else:
        if not _may_fetch_its_own_page(settings):
            return False
        fetched = await _fetch_article_page(
            article, settings, client, robots, rate_limiter
        )
        text = body_text_from_extracted(fetched)

    if not text:
        return False

    # 3. Fail closed. The licence is re-read at the point of storage
    #    so that a source row which never carried the flag — or lost
    #    it between the check above and here — stores nothing at all.
    #    base.validate() cannot cover this: it runs before, and sees a
    #    field that is still None here.
    if not permits_full_text(source_row):
        logger.warning(
            "body: licence withdrawn mid-run; dropping body for %r",
            (getattr(article, "headline", "") or "")[:60],
        )
        return False

    article.full_body_html = text
    logger.info(
        "body: stored %d chars of licensed body for %r",
        len(text),
        (getattr(article, "headline", "") or "")[:60],
    )
    return True