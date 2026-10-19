"""
Provider adapters for short-form video we are actually allowed to republish.

What this is
------------
Three sources of genuinely redistributable clips behind one interface.
Each publishes terms that permit republication; that is the only reason
a `<video>` can appear in front of a reader who never asked us. Sources
with murky terms are not here, and neither are scrapers.

Why three and not one
---------------------
- Wikimedia Commons needs no key and no configuration, which is the
  whole reason the category is alive on a fresh deploy. Its clips are
  also the least reliably vertical.
- Pexels and Pixabay are the two stock libraries whose free tiers
  genuinely allow publication. They need a key, and they are what make
  the feed read like a feed once one is configured.

Why nothing is downloaded
-------------------------
A reel row is metadata plus an upstream URL; playback streams from the
provider's own CDN. Reels are the largest objects in the system and
storing the bytes would buy a fatter database and a bandwidth bill.

Licence handling
----------------
Attribution is filled in whether or not the licence demands display.
"No attribution required" is a claim about the licence, never a claim
that the clip is ours, and only the first of those is true.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from app.services.reels.keys import KeyPool, KeyPoolExhausted
from app.services.reels.types import Reel, ReelSource

logger = logging.getLogger("reels.providers")


# ---------------------------------------------------------------------------
# POLICY
# ---------------------------------------------------------------------------

#: Anything longer than this is a clip, not a reel. A three-minute
#: Commons documentary segment is not what a vertical feed is for, and
#: letting one in makes the whole category feel like it is lying.
MAX_SECONDS = 180.0

#: Floor for a "playable" rendition. Picking a 180px variant is how you
#: get a card that renders beautifully and looks like garbage.
MIN_HEIGHT = 360
PREFERRED_HEIGHT = 720

#: Wikimedia asks every API client to identify itself. Keep this in sync
#: with the deployment's real contact details — an unidentifiable bot is
#: exactly how a project gets its API access withdrawn.
USER_AGENT = "NewsMagazineReels/1.0"


class ProviderError(RuntimeError):
    """
    One provider failed.

    Deliberately distinct from KeyPoolExhausted: nothing is wrong with
    the provider, we simply have no quota left, and the two call for
    opposite responses (record a degraded source vs. sleep).
    """


# ---------------------------------------------------------------------------
# SHARED PARSING
# ---------------------------------------------------------------------------

def _absolute(value: Any) -> str | None:
    """
    Normalize a URL to absolute http(s).

    MediaWiki hands back protocol-relative URLs (`//upload.wikimedia…`)
    and several providers occasionally do too. A scheme-less URL in a
    `<video src>` is a card that silently never plays.
    """
    if not isinstance(value, str):
        return None

    text = html.unescape(value).strip()
    if not text:
        return None

    if text.startswith("//"):
        text = "https:" + text

    if text.startswith("/"):
        return None  # No base to resolve against; guessing one would lie.

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return text


def _plain_text(value: Any) -> str | None:
    """Flatten an HTML-bearing metadata value into readable text."""
    if value is None:
        return None

    # MediaWiki wraps extmetadata entries as {"value": ..., "source": ...}.
    # The wrapper is unwrapped at the merge point, but accepting it here
    # too means a field read from either module is readable either way.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]

    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:  # pragma: no cover - defensive
            return None

    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    return text or None


def _first_href(value: Any) -> str | None:
    """First link in an HTML-bearing metadata value, absolute."""
    if isinstance(value, dict) and "value" in value:
        value = value["value"]

    if not isinstance(value, str):
        return None

    match = re.search(r"""<a\b[^>]*\bhref=["']([^"']+)["']""", value, re.IGNORECASE)
    if not match:
        return None

    href = html.unescape(match.group(1)).strip()
    if href.startswith("//"):
        return "https:" + href

    if href.startswith("/"):
        return urljoin("https://commons.wikimedia.org", href)

    return _absolute(href)


def _number(value: Any) -> float | None:
    """Coerce to a number, or None. Providers send both str and int."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _clamp_duration(value: Any) -> float | None:
    return _number(value)


def _parse_retry_after(value: str | None) -> float | None:
    """
    Seconds to wait, from a Retry-After header.

    The header is allowed to be either a delay in seconds or an HTTP
    date, and both free tiers we target have used each. Guessing an hour
    when the provider said "90 seconds" burns an hour of quota.
    """
    if not value:
        return None

    text = value.strip()

    try:
        return max(0.0, float(text))
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None

    if when is None:
        return None

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _within_duration(seconds: float | None) -> bool:
    """True when the clip is short enough (or simply unknown)."""
    if seconds is None:
        return True
    return 0 < seconds <= MAX_SECONDS


def _sorted_by_timestamp(reels: list[Reel]) -> list[Reel]:
    """
    Newest first, as the fetch contract promises.

    Undated items sort last rather than pretending to be old; a reel with
    no timestamp is a reel we know nothing about.
    """
    def sort_key(reel: Reel) -> tuple[int, str]:
        if reel.published_at:
            return (0, reel.published_at)
        return (1, "")

    # Reverse=True on the ISO string works because every timestamp here
    # is UTC ISO-8601, which sorts lexicographically in time order.
    return sorted(reels, key=sort_key, reverse=True)


# ---------------------------------------------------------------------------
# LICENCE TABLE (Wikimedia)
# ---------------------------------------------------------------------------

# MediaWiki's `License` code (e.g. "cc-by-sa-4.0") is the only licence
# field present on essentially every Commons file, so it is what the
# fallback path resolves against.
_LICENSE_NAMES = {
    "cc0": "CC0",
    "cc-zero": "CC0",
    "public domain": "Public domain",
    "pd": "Public domain",
}

_LICENSE_URLS = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc-zero": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc-zero-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}

_PUBLIC_DOMAIN_HOSTS = (
    "creativecommons.org/publicdomain",
    "creativecommons.org/publicdomain/mark",
)


def _license_url_from_code(code: str) -> str | None:
    """cc-by-sa-4.0 -> the canonical licence page."""
    if not code.startswith("cc-"):
        return None

    parts = code.split("-")
    if len(parts) < 2:
        return None

    # cc-by-sa-4.0 -> ["by", "sa", "4.0"]; cc-by-nc-sa-3.0 -> ["by","nc","sa","3.0"]
    version = parts[-1]
    elements = "-".join(parts[1:-1])
    if not elements or not re.match(r"^\d+(\.\d+)?$", version):
        return None

    return (
        f"https://creativecommons.org/licenses/{elements}/{version}/"
    )


def _license_from_extmetadata(meta: dict) -> tuple[str, str | None, bool] | None:
    """
    Work out (license_name, license_url, attribution_required).

    Returns None when the file has no recognisable free licence. We skip
    those files rather than publish them: "Commons" is not a licence,
    and guessing is how a feed ends up redistributing something it has
    no right to.
    """
    short = _plain_text(meta.get("LicenseShortName"))
    code = (_plain_text(meta.get("License")) or "").lower()
    legacy = (_plain_text(meta.get("LicenseValue")) or "").lower()
    url = _absolute(_plain_text(meta.get("LicenseUrl"))) or None

    haystack = " ".join(filter(None, [short, code, legacy, url])).lower()

    is_cc0 = "cc0" in haystack or "cc-zero" in haystack
    is_public_domain = (
        "public domain" in haystack
        or any(host in haystack for host in _PUBLIC_DOMAIN_HOSTS)
        or code in {"pd", "public domain"}
        or code.startswith("pd-")
        or "copyrighted free use" in haystack
    )
    is_free_licensed = (
        is_cc0
        or is_public_domain
        or code.startswith("cc-")
        or "creativecommons.org" in haystack
    )

    if not is_free_licensed:
        return None

    name = short
    if not name:
        name = _LICENSE_NAMES.get(code) or _LICENSE_NAMES.get(legacy)
    if not name and code.startswith("cc-"):
        name = code.upper().replace("CC-", "CC ", 1)
    if not name:
        name = "Creative Commons"
    if is_cc0 and "cc0" not in name.lower():
        name = "CC0 1.0"

    if not url:
        url = _LICENSE_URLS.get(code) or _license_url_from_code(code)

    # Commons publishes an explicit attribution flag; trust it when it is
    # present, because a file marked CC-BY-*-NO still requires credit and a
    # heuristic on the name alone would get that wrong.
    #
    # The default is True and only a licence that positively waives credit
    # may turn it off. Getting this backwards is the worst kind of bug in
    # this file: it would mark CC BY — which mandates credit — as needing
    # none, and the credit would be dropped for exactly the clips whose
    # creators are legally entitled to it.
    attribution_required = True
    flag = (_plain_text(meta.get("AttributionRequired")) or "").strip().lower()
    if flag in {"true", "1", "yes"}:
        attribution_required = True
    elif flag in {"false", "0", "no"}:
        attribution_required = False
    elif is_cc0 or is_public_domain:
        attribution_required = False

    return name, url, attribution_required


# ---------------------------------------------------------------------------
# BASE
# ---------------------------------------------------------------------------

class _BaseSource:
    """
    Shared plumbing: the JSON call, the key rotation, the error boundary.

    Subclasses describe *what* they call. Everything that is the same
    across providers — honouring Retry-After, not letting one failure
    become everyone else's, never emitting unattributed media — lives
    here so a new provider cannot forget it.
    """

    #: Stable id written to reels.source_key.
    key: str = ""

    #: Whether this provider is unusable without an API key.
    requires_key: bool = True

    #: Human-readable name, used as the creator fallback.
    provider_name: str = ""

    #: Where `fetch` goes.
    endpoint: str = ""

    def __init__(self, *, pool: KeyPool | None, client: httpx.AsyncClient, hint: str = "") -> None:
        self._pool = pool
        self._client = client
        self.hint = _plain_text(hint) or ""
        if self.hint:
            # A hint comes from operator config and ends up in a search
            # string; bound it so a pasted paragraph cannot become a 4KB
            # query parameter.
            self.hint = self.hint[:200]

    # -------------------------------------------------
    # HTTP
    # -------------------------------------------------

    def _headers(self, key: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.requires_key and key:
            headers["Authorization"] = key
        return headers

    def _require_pool(self) -> KeyPool:
        """
        The pool, or a precise complaint.

        An unconfigured provider is a configuration problem, not a quota
        problem, and reporting it as one would send the orchestrator into
        a backoff loop for a key that will never appear.
        """
        if self._pool is None or self._pool.is_empty:
            raise ProviderError(
                f"{self.key}: no API key configured. Set {self.key.upper()}_API_KEY "
                f"to enable this source."
            )
        return self._pool

    async def _get_json(self, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict:
        """
        One JSON GET, with key rotation.

        A 429 cools the key it was made with and tries the next one; when
        the last key is cooling, `next_key()` raises KeyPoolExhausted and
        that propagates untouched, because a full pool is the
        orchestrator's backoff decision and not a provider failure.
        """
        pool = self._require_pool() if self.requires_key else self._pool

        # An EMPTY pool means exactly what no pool means, and treating it
        # as "has keys to rotate" is what stopped the one provider that
        # needs no configuration from working at all: build_pool() hands
        # every source a KeyPool, so Wikimedia arrives here with a pool
        # of zero keys rather than None. `attempts` was 1, `next_key()`
        # raised KeyPoolExhausted("All 0 key(s) are rate limited"), and
        # the category stayed empty forever on a deploy with nothing set
        # — while every test, which built the adapter with pool=None,
        # passed.
        #
        # A provider that DOES require a key cannot reach this line with
        # an empty pool: _require_pool() raises first.
        if pool is None or pool.is_empty:
            # No key to rotate, so no reason to rotate anything. Same
            # status handling, minus the bookkeeping.
            return self._decode(await self._send(None, params, headers))

        # Bounded so a pathological pool cannot spin. next_key() raises
        # first in practice once every key is in cooldown.
        attempts = len(pool) + 1

        for _ in range(attempts):
            key = pool.next_key()
            response = await self._send(key, params, headers)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("retry-after"))
                pool.mark_quota_exceeded(key, retry_after=retry_after)
                logger.info(
                    "%s: key rate limited, retrying with the next one.", self.key
                )
                continue

            if response.status_code == 401 or response.status_code == 403:
                # A rejected credential is a credential, not a quota
                # event. Do not cool it; the operator has to fix it.
                pool.mark(key, ok=False)
                raise ProviderError(
                    f"{self.key}: the API key was rejected (HTTP {response.status_code}). "
                    f"Check {self.key.upper()}_API_KEY."
                )

            if response.status_code >= 400:
                pool.mark(key, ok=False)
                raise ProviderError(
                    f"{self.key}: upstream returned HTTP {response.status_code}."
                )

            pool.mark(key, ok=True)

            return self._decode(response)

        raise ProviderError(f"{self.key}: exhausted {attempts} key attempts without a usable response.")

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        """A JSON object, or a ProviderError that says why there isn't one."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{response.request.url.host}: upstream returned a body that is not JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                f"{response.request.url.host}: upstream returned "
                f"{type(payload).__name__}, expected an object."
            )

        # MediaWiki answers HTTP 200 with the failure in the body. An
        # unread `error` key here is how a bad parameter turned into a
        # provider that returned zero reels and reported itself healthy
        # for the life of the process — indistinguishable from "Commons
        # simply has nothing new". A 200 is not proof the call worked.
        api_error = payload.get("error")
        if isinstance(api_error, dict):
            code = api_error.get("code", "unknown")
            info = str(api_error.get("info", ""))[:200]
            raise ProviderError(
                f"{response.request.url.host}: API error {code}: {info}"
            )

        return payload

    async def _send(
        self,
        key: str | None,
        params: dict[str, Any],
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        merged = {"User-Agent": USER_AGENT}
        merged.update(self._headers(key))
        if headers:
            merged.update(headers)

        try:
            return await self._client.get(self.endpoint, params=params, headers=merged)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.key}: request failed: {exc}") from exc

    # -------------------------------------------------
    # Contract
    # -------------------------------------------------

    async def fetch(self, limit: int = 30) -> list[Reel]:
        """
        Candidates for this pass, newest first.

        Wraps everything into ProviderError. A provider failing is that
        provider's problem; letting its traceback through would abort
        the pass and starve the sources that are working.
        """
        try:
            reels = await self._fetch(limit)
        except KeyPoolExhausted:
            raise
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary on purpose
            raise ProviderError(
                f"{self.key}: {type(exc).__name__}: {exc}"
            ) from exc

        reels = reels[:limit]
        logger.info(
            "%s: %d reel(s) from hint %r.", self.key, len(reels), self.hint or "*"
        )
        return reels

    async def _fetch(self, limit: int) -> list[Reel]:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------------------
# WIKIMEDIA COMMONS
# ---------------------------------------------------------------------------

class WikimediaSource(_BaseSource):
    """
    Wikimedia Commons, the source that needs nothing configured.

    Every file on Commons carries an explicit licence in its metadata,
    which makes it the one video source on this list where we can prove
    redistribution is allowed rather than assume it. That, and not the
    footage, is why this class is first.

    Why it is safe to redistribute
    ------------------------------
    Commons accepts a file only when its licence permits free use. We
    re-read that licence per file rather than trusting the upload, we
    refuse to emit any file whose licence we cannot read, and we always
    ship the author and licence with the clip. A file with an unclear
    or missing licence is skipped entirely.

    On codecs
    ---------
    Commons masters are .webm and .ogv, and .ogv will not play in half
    the browsers we care about. Wikimedia transcodes every video into
    browser-safe mp4 at several heights and those live in `derivatives`,
    so we take an mp4 transcode when one exists and fall back to the
    webm master only when it does not.
    """

    key = "wikimedia"
    requires_key = False
    provider_name = "Wikimedia Commons"
    endpoint = "https://commons.wikimedia.org/w/api.php"

    #: Commons transcodes land on upload.wikimedia.org; the originals and
    #: the JPEG posters do too, so those hosts are safe to serve.
    _ALLOWED_HOSTS = ("upload.wikimedia.org",)

    async def _fetch(self, limit: int) -> list[Reel]:
        # Over-fetch. Most candidates get dropped for being longer than
        # three minutes or for having an unreadable licence, and
        # requesting exactly `limit` would return a short feed.
        wanted = min(50, max(limit, 10) * 3)

        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": self._search_expression(),
            "gsrnamespace": "6",  # File:
            "gsrlimit": str(wanted),
            # Newest uploads first, so a repeated run actually finds new
            # material instead of returning the same top-relevance files
            # until someone renames them.
            #
            # "indexed" and "indexed_desc" are NOT accepted by Commons —
            # CirrusSearch does not offer them for this generator — and a
            # bad sort answers HTTP 200 with an `error` object in the
            # body rather than a 4xx. create_timestamp_desc is the
            # "newest first" ordering and is supported.
            "gsrsort": "create_timestamp_desc",
            # imageinfo gives us the JPEG poster; videoinfo gives us the
            # mp4 transcodes and the running time. Neither alone is
            # enough, so both are requested for the same titles.
            "prop": "imageinfo|videoinfo",
            "iiprop": "url|size|mime|mediatype|extmetadata",
            "iiurlwidth": "720",
            "viprop": "url|size|mime|mediatype|derivatives|extmetadata",
        }

        payload = await self._get_json(params)

        pages = (payload.get("query") or {}).get("pages") or []
        if not isinstance(pages, list):
            pages = []

        reels: list[Reel] = []
        skipped_no_licence = 0
        skipped_too_long = 0

        for page in pages:
            if not isinstance(page, dict):
                continue

            reel, reason = self._to_reel(page)
            if reel is not None:
                reels.append(reel)
            elif reason == "no_licence":
                skipped_no_licence += 1
            elif reason == "too_long":
                skipped_too_long += 1

        if skipped_no_licence or skipped_too_long:
            logger.debug(
                "wikimedia: skipped %d file(s) with no readable free licence "
                "and %d longer than %ds.",
                skipped_no_licence, skipped_too_long, int(MAX_SECONDS),
            )

        return _sorted_by_timestamp(reels)

    # -------------------------------------------------
    # Search
    # -------------------------------------------------

    def _search_expression(self) -> str:
        """
        A CirrusSearch expression, not a bag of words.

        `filetype:video` keeps us out of the ogg-audio pile, and the
        non-free category is negated explicitly: those files are on
        Commons under fair-use claims, which is precisely the material
        we must never put in our own feed.
        """
        parts = ["filetype:video", '-incategory:"Non-free files"']

        if self.hint:
            parts.append(self.hint)

        return " ".join(parts)

    # -------------------------------------------------
    # Parsing
    # -------------------------------------------------

    def _to_reel(self, page: dict) -> tuple[Reel | None, str | None]:
        """One Commons file -> one Reel, or a reason it was dropped."""

        imageinfo = self._first_info(page.get("imageinfo"))
        videoinfo = self._first_info(page.get("videoinfo"))

        # extmetadata is duplicated on both modules; the video one is
        # the more complete of the two for video files.
        meta = self._merge_extmetadata(imageinfo, videoinfo)

        licence = _license_from_extmetadata(meta)
        if licence is None:
            return None, "no_licence"
        license_name, license_url, attribution_required = licence

        duration = _clamp_duration((videoinfo or {}).get("duration"))
        if not _within_duration(duration):
            return None, "too_long"

        title = _plain_text(page.get("title")) or ""
        file_name = title[5:] if title.lower().startswith("file:") else title

        video_url = self._best_video_url(videoinfo, imageinfo)
        if not video_url:
            return None, "no_playable_derivative"

        original_url = _absolute((imageinfo or {}).get("url")) or _absolute(
            (videoinfo or {}).get("url")
        )

        width = _int_or_none((videoinfo or {}).get("width")) or _int_or_none(
            (imageinfo or {}).get("width")
        )
        height = _int_or_none((videoinfo or {}).get("height")) or _int_or_none(
            (imageinfo or {}).get("height")
        )

        description = (
            _plain_text(meta.get("ImageDescription"))
            or _plain_text(meta.get("ObjectName"))
        )

        timestamp = (videoinfo or {}).get("timestamp") or (imageinfo or {}).get("timestamp")
        published_at = _iso(timestamp) or _iso(meta.get("DateTimeOriginal"))

        page_id = page.get("pageid")
        description_url = _absolute((videoinfo or {}).get("descriptionurl")) or _absolute(
            (imageinfo or {}).get("descriptionurl")
        )

        creator_name, creator_url = self._creator(meta)

        return (
            Reel(
                source_key=self.key,
                external_id=str(page_id) if page_id else title or original_url or video_url,
                video_url=video_url,
                poster_url=self._poster_url(imageinfo, original_url),
                title=file_name or None,
                description=description,
                creator_name=creator_name,
                creator_url=creator_url,
                license_name=license_name,
                license_url=license_url,
                attribution_required=attribution_required,
                width=width,
                height=height,
                duration_seconds=duration,
                published_at=published_at,
            ),
            None,
        )

    @staticmethod
    def _first_info(value: Any) -> dict | None:
        """MediaWiki returns a single-element list; take the head."""
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
            return None
        if isinstance(value, dict):
            return value
        return None

    @staticmethod
    def _merge_extmetadata(*blocks: dict | None) -> dict:
        """
        Later blocks win; either module may carry a field the other lacks.

        MediaWiki wraps every extmetadata entry as
        `{"value": ..., "source": ...}` so it can carry provenance
        alongside the text. Everything downstream wants the text, so it
        is unwrapped exactly once, here, where the shape is known.
        """
        merged: dict[str, Any] = {}
        for block in blocks:
            meta = (block or {}).get("extmetadata")
            if not isinstance(meta, dict):
                continue
            for field_name, entry in meta.items():
                if isinstance(entry, dict) and "value" in entry:
                    merged[field_name] = entry["value"]
                else:
                    merged[field_name] = entry
        return merged

    def _best_video_url(self, videoinfo: dict | None, imageinfo: dict | None) -> str | None:
        """
        Prefer a transcoded mp4, fall back to the original master.

        Commons transcodes every video to h.264 mp4 at several heights
        and advertises them in `derivatives`. Safari and every iOS
        browser refuse to play webm or ogv, so an mp4 transcode is the
        difference between a working reel and a black rectangle on the
        platform that matters most.
        """
        candidates: list[tuple[int, int, int, str]] = []

        for derivative in (videoinfo or {}).get("derivatives") or []:
            if not isinstance(derivative, dict):
                continue
            url = _absolute(derivative.get("src"))
            if not url or not self._is_allowed_host(url):
                continue

            mime = (derivative.get("type") or "").lower()
            height = _int_or_none(derivative.get("height")) or 0
            width = _int_or_none(derivative.get("width")) or 0

            if mime == "video/mp4":
                # Rank 0: browser-safe.
                rank = 0
            elif mime.startswith("video/webm"):
                # Rank 1: plays in Chromium and Firefox.
                rank = 1
            elif mime.startswith("video/"):
                rank = 2
            else:
                continue

            candidates.append((rank, height, width, url))

        if candidates:
            # Biggest first inside a rank, so a phone gets the 720p and
            # not the 240p.
            candidates.sort(key=lambda item: (item[0], -item[1], -item[2], item[3]))
            return candidates[0][3]

        # No transcodes at all (an .ogv that predates transcoding, say).
        # The original is still hotlinkable and still webm/ogv.
        for block in (videoinfo, imageinfo):
            url = _absolute((block or {}).get("url"))
            if url and self._is_allowed_host(url):
                return url

        return None

    def _is_allowed_host(self, url: str) -> bool:
        """Only serve bytes from Wikimedia's own media host."""
        host = (urlparse(url).hostname or "").lower()
        return host in self._ALLOWED_HOSTS

    def _poster_url(self, imageinfo: dict | None, original_url: str | None) -> str | None:
        """
        The still frame MediaWiki generated for the video.

        `thumburl` is the real poster when it is there. The reconstructed
        URL is only a fallback: its exact filename convention is a
        detail of MediaWiki's thumbnailing, and if it ever changes the
        worst case is a card that renders without a poster rather than a
        broken request.
        """
        thumb = _absolute((imageinfo or {}).get("thumburl"))
        if thumb and self._is_allowed_host(thumb):
            return thumb

        if not original_url or not self._is_allowed_host(original_url):
            return None

        path = urlparse(original_url).path
        # /wikipedia/commons/a/ab/Foo.webm
        parts = [p for p in path.split("/") if p]
        if len(parts) < 5 or parts[0] != "wikipedia":
            return None

        thumb_dir = parts[:-1]
        file_name = parts[-1]
        return (
            "https://upload.wikimedia.org/"
            + "/".join(thumb_dir)
            + "/thumb"
            + "/" + "/".join(thumb_dir[1:])
            + f"/720px--{quote(file_name)}.jpg"
        )

    def _creator(self, meta: dict) -> tuple[str, str | None]:
        """
        Author, then uploader, then the provider.

        Commons files carry an author for the work and a credit line for
        the upload, and the author is the one we owe. When both are
        absent the file is an orphan and naming the provider is the only
        honest attribution we can give — an empty creator would render a
        card with nothing on it and imply nobody made this.
        """
        artist_raw = meta.get("Artist") or meta.get("Attribution")
        credit_raw = meta.get("Credit")

        for raw in (artist_raw, credit_raw):
            name = _plain_text(raw)
            if name:
                url = _first_href(raw) or _first_href(credit_raw)
                return name, url

        # No author field at all. Say where the file came from rather
        # than emitting an unattributed reel.
        return self.provider_name, "https://commons.wikimedia.org/"


def _iso(value: Any) -> str | None:
    """MediaWiki timestamps are already ISO-8601; validate and pass on."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PEXELS
# ---------------------------------------------------------------------------

class PexelsSource(_BaseSource):
    """
    Pexels stock video.

    Why it is safe to redistribute
    ------------------------------
    The Pexels licence grants free use for commercial and non-commercial
    projects with no attribution requirement. That is a permission, not a
    gift: we still record who shot every clip, because "credit not
    required" is a property of their licence and not a statement that
    the footage is unowned.

    The catch is the API key. There is no anonymous tier, so this source
    contributes nothing until one is configured — which is exactly why
    Wikimedia carries this category on its own.
    """

    key = "pexels"
    requires_key = True
    provider_name = "Pexels"
    endpoint = "https://api.pexels.com/videos/search"

    LICENSE_NAME = "Pexels License"
    LICENSE_URL = "https://www.pexels.com/license/"

    #: Pexels sometimes omits width/height on a rendition and gives only
    #: a quality word. These are their documented bucket sizes.
    _QUALITY_HEIGHT = {
        "tiny": 180,
        "small": 360,
        "medium": 480,
        "large": 720,
        "hd": 1080,
    }

    async def _fetch(self, limit: int) -> list[Reel]:
        params = {
            "query": self.hint or "vertical",
            "per_page": str(min(80, max(limit, 10) * 2)),
            # A reels feed is vertical. A landscape clip in a vertical
            # card is not a styling problem, it is the wrong content.
            "orientation": "portrait",
        }

        payload = await self._get_json(params)

        videos = payload.get("videos")
        if not isinstance(videos, list):
            return []

        reels: list[Reel] = []
        skipped_too_long = 0

        for video in videos:
            if not isinstance(video, dict):
                continue

            duration = _clamp_duration(video.get("duration"))
            if not _within_duration(duration):
                skipped_too_long += 1
                continue

            rendition = self._pick_rendition(video.get("video_files"))
            if rendition is None:
                continue

            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            creator_name = _plain_text(user.get("name")) or self.provider_name

            external_id = video.get("id")
            page_url = _absolute(video.get("url"))

            reels.append(
                Reel(
                    source_key=self.key,
                    external_id=str(external_id) if external_id else rendition["url"],
                    video_url=rendition["url"],
                    poster_url=_absolute(video.get("image")),
                    title=_plain_text(video.get("title")),
                    creator_name=creator_name,
                    creator_url=_absolute(user.get("url")) or page_url,
                    license_name=self.LICENSE_NAME,
                    license_url=self.LICENSE_URL,
                    # Not required by their licence. Recorded anyway.
                    attribution_required=False,
                    width=rendition["width"] or _int_or_none(video.get("width")),
                    height=rendition["height"] or _int_or_none(video.get("height")),
                    duration_seconds=duration,
                    published_at=None,  # The video search endpoint returns no date.
                )
            )

        if skipped_too_long:
            logger.debug(
                "pexels: skipped %d clip(s) longer than %ds.",
                skipped_too_long, int(MAX_SECONDS),
            )

        # No timestamp is available, so the provider's own ranking is the
        # best ordering we have. It is relevance-ordered, not
        # date-ordered, and pretending otherwise would be a lie in a
        # field the UI reads as a date.
        return reels

    def _pick_rendition(self, files: Any) -> dict | None:
        """
        The largest usable mp4.

        Pexels returns one entry per rendition and the list includes
        genuinely tiny previews. Shipping a 180px thumbnail as the reel
        looks like a broken site, so the floor is enforced before
        quality is compared.
        """
        if not isinstance(files, list):
            return None

        playable: list[dict] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            url = _absolute(entry.get("link"))
            if not url:
                continue
            # HLS is a playlist, not a file; a <video src> pointed at one
            # either needs hls.js or plays nothing.
            file_type = (entry.get("file_type") or "").lower()
            if file_type == "video/hls":
                continue
            playable.append(entry)

        if not playable:
            return None

        def measure(entry: dict) -> tuple[int, int, int]:
            height = _int_or_none(entry.get("height")) or self._QUALITY_HEIGHT.get(
                (entry.get("quality") or "").lower(), 0
            )
            width = _int_or_none(entry.get("width")) or 0
            return height, width, _int_or_none(entry.get("size")) or 0

        # Prefer HD; fall back to the largest thing above the floor.
        for floor in (PREFERRED_HEIGHT, MIN_HEIGHT):
            usable = [e for e in playable if measure(e)[0] >= floor]
            if usable:
                break
        else:
            return None

        best = max(
            usable,
            key=lambda e: (measure(e)[0], measure(e)[1], measure(e)[2]),
        )
        height, width, _ = measure(best)

        return {
            "url": _absolute(best.get("link")),
            "height": height or None,
            "width": width or None,
        }


# ---------------------------------------------------------------------------
# PIXABAY
# ---------------------------------------------------------------------------

class PixabaySource(_BaseSource):
    """
    Pixabay stock video.

    Why it is safe to redistribute
    ------------------------------
    The Pixabay Content License permits free use and modification,
    including commercially, with no attribution required. Same posture
    as Pexels: the licence waives the credit requirement, it does not
    erase the photographer, so the uploader is recorded on every clip.

    Like Pexels this needs a key. It is kept as a separate adapter
    anyway because its quota, its rate limits and its failure modes are
    its own, and a shared adapter would mean one provider's outage
    silently disabled both.
    """

    key = "pixabay"
    requires_key = True
    provider_name = "Pixabay"
    endpoint = "https://pixabay.com/api/videos/"

    LICENSE_NAME = "Pixabay Content License"
    LICENSE_URL = "https://pixabay.com/service/license-summary/"

    async def _fetch(self, limit: int) -> list[Reel]:
        params = {
            "q": self.hint or "vertical",
            "per_page": str(min(200, max(limit, 10) * 3)),
            "safesearch": "true",
            # Pixabay answers in XML unless asked for JSON; this is the
            # documented switch and it is not optional.
            "json": "1",
        }

        payload = await self._get_json(params)

        # The API wraps hits in "videos" and the count in "videoCount",
        # and for a single hit "videos" arrives as an object rather than
        # a list. Both shapes are real and both have to work.
        hits = payload.get("videos")
        if isinstance(hits, dict):
            hits = [hits]
        if not isinstance(hits, list):
            return []

        reels: list[Reel] = []
        skipped_too_long = 0

        for hit in hits:
            if not isinstance(hit, dict):
                continue

            duration = _clamp_duration(hit.get("duration"))
            if not _within_duration(duration):
                skipped_too_long += 1
                continue

            url, width, height = self._pick_variant(hit.get("videos"))
            if url is None:
                continue

            external_id = hit.get("id")
            page_url = _absolute(hit.get("pageURL"))

            reels.append(
                Reel(
                    source_key=self.key,
                    external_id=str(external_id) if external_id else url,
                    video_url=url,
                    poster_url=_absolute(hit.get("videoImage")),
                    title=self._title(hit),
                    creator_name=self._creator(hit),
                    creator_url=self._creator_url(hit),
                    license_name=self.LICENSE_NAME,
                    license_url=self.LICENSE_URL,
                    # Not required by their licence. Recorded anyway.
                    attribution_required=False,
                    width=width or _int_or_none(hit.get("width")),
                    height=height or _int_or_none(hit.get("height")),
                    duration_seconds=duration,
                    published_at=_iso(hit.get("last_updated")) or _iso(hit.get("created")),
                )
            )

        if skipped_too_long:
            logger.debug(
                "pixabay: skipped %d clip(s) longer than %ds.",
                skipped_too_long, int(MAX_SECONDS),
            )

        return _sorted_by_timestamp(reels)

    def _pick_variant(self, variants: Any) -> tuple[str | None, int | None, int | None]:
        """
        The largest direct-download variant.

        `videoURL` at the top level of a hit is the share page, an HTML
        document, not a video. Putting it in `video_url` would produce a
        reel that renders and refuses to play, so only the nested
        variant list is considered.
        """
        if not isinstance(variants, list):
            return None, None, None

        best: tuple[int, int, int] | None = None
        best_url: str | None = None

        for variant in variants:
            if not isinstance(variant, dict):
                continue
            url = _absolute(variant.get("url"))
            if not url:
                continue

            height = _int_or_none(variant.get("height")) or 0
            width = _int_or_none(variant.get("width")) or 0
            size = _int_or_none(variant.get("size")) or 0

            if height and height < MIN_HEIGHT:
                continue

            score = (height, width, size)
            if best is None or score > best:
                best = score
                best_url = url

        if best_url is None:
            return None, None, None

        return best_url, (best[1] or None), (best[0] or None)

    @staticmethod
    def _title(hit: dict) -> str | None:
        """Pixabay has no title field; tags are the closest honest thing."""
        tags = hit.get("tags")
        if isinstance(tags, str) and tags.strip():
            return f"{hit.get('id')}: {tags.strip()}"[:200]
        return None

    def _creator(self, hit: dict) -> str:
        name = _plain_text(hit.get("user"))
        if name:
            return name
        # Pixabay occasionally lists a video with no uploader after the
        # account is removed. The provider is still the true origin.
        return self.provider_name

    @staticmethod
    def _creator_url(hit: dict) -> str | None:
        user_id = hit.get("user_id")
        if user_id:
            return f"https://pixabay.com/users/{user_id}/"
        return _absolute(hit.get("pageURL"))


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, type] = {
    "wikimedia": WikimediaSource,
    "pexels": PexelsSource,
    "pixabay": PixabaySource,
}


def build_source(key: str, *, pool: KeyPool | None, client: httpx.AsyncClient, hint: str = "") -> ReelSource:
    """
    Instantiate the adapter named by `key`.

    Raises KeyError for an unknown key so the caller can record a
    precise "unknown_source" error rather than silently skipping it.
    `pool` may be None for sources that need no key.
    """
    normalized = (key or "").strip().lower()
    source_cls = PROVIDERS.get(normalized)

    if source_cls is None:
        raise KeyError(
            f"No reels source registered for {key!r}. "
            f"Known keys: {', '.join(sorted(PROVIDERS))}."
        )

    return source_cls(pool=pool, client=client, hint=hint)