"""
Provider adapters for short-form video we are actually allowed to republish.

What this is
------------
Three sources of genuinely redistributable clips behind one interface.
Each publishes terms that permit republication; that is the only reason
a `<video>` can appear in front of a reader who never asked us. Sources
with murky terms are not here, and neither are scrapers.

Why four and not one
--------------------
- Wikimedia Commons needs no key and no configuration, which is the
  whole reason the category is alive on a fresh deploy. Its clips are
  also the least reliably vertical.
- Pexels and Pixabay are the two stock libraries whose free tiers
  genuinely allow publication. They need a key, and they are what make
  the feed read like a feed once one is configured.
- YouTube Shorts is the odd one out and is NOT a redistributable
  source like the other three. We do not republish its clips; we embed
  them through YouTube's own player, which is the sharing mechanism
  YouTube provides and the only one that needs no credential we do not
  have. Read YouTubeShortsSource before treating its rows like the
  others — it plays in an <iframe>, and its "licence" is a permission
  for the uploader, not for us.

Why nothing is downloaded
-------------------------
A reel row is metadata plus an upstream URL; playback streams from the
provider's own CDN or its own player. Reels are the largest objects in
the system and storing the bytes would buy a fatter database and a
bandwidth bill.

Licence handling
----------------
Attribution is filled in whether or not the licence demands display.
"No attribution required" is a claim about the licence, never a claim
that the clip is ours, and only the first of those is true.
YouTube is the exception that proves the rule: every row it produces
carries attribution_required=True, because its licence is not ours to
read as permissive in the first place.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
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

#: Pause between the per-term searches in one Wikimedia pass.
#:
#: A pass fires up to twelve of them back to back. That is a burst, and
#: CirrusSearch's documented answer to a burst is
#: `cirrussearch-too-busy-error`. Half a second costs five seconds on a
#: pass with a three-minute ceiling, and removes most of the pressure
#: that produces the error in the first place.
SEARCH_PACING_SECONDS = 0.5

#: How hard to ride out a "search is currently too busy" answer.
#:
#: Commons returns it as HTTP 200 with the error inside the body, so it
#: looks like a success to anything that only checks the status code.
#: Three attempts — two, four seconds — clears a busy minute without
#: holding the scheduler slot, which runs at `max_instances=1`.
BUSY_RETRY_ATTEMPTS = 3
BUSY_RETRY_BASE_SECONDS = 2.0

#: Largest page of search results we will ask Commons for in one request.
#:
#: Commons silently discards the entire `videoinfo` module — returning
#: HTTP 200, no error object, no warning — once a request combines a
#: large page with `iiurlwidth`. Measured against the live API, same
#: query, varying only `gsrlimit`:
#:
#:   gsrlimit= 20 + iiurlwidth -> 20 pages, 20 with videoinfo
#:   gsrlimit= 40 + iiurlwidth -> 40 pages, 40 with videoinfo
#:   gsrlimit= 50 + iiurlwidth -> 50 pages,  0 with videoinfo
#:
#: Fifty is the value this adapter used, so in production every Wikimedia
#: pass was storing reels built from `imageinfo` alone. Two things break
#: at once and neither announces itself:
#:
#:   1. `duration` is gone, and `_within_duration(None)` returns True —
#:      so the three-minute cap silently stopped applying.
#:   2. `derivatives` is gone, and that is the list `_best_video_url`
#:      picks a playable transcode from. With no derivatives it falls
#:      back to the ORIGINAL master file, so the feed pointed phones at
#:      Commons originals — measured at 159MB, 164MB and 807MB — and
#:      labelled them by a resolution derived from the master rather
#:      than the bytes actually served.
#:
#: Twenty-five leaves ample headroom under the observed cliff while
#: still asking for a useful page, and `_search_with_retry` re-checks
#: for the dropped module so a future move of the cliff degrades to
#: "fewer rows" instead of "800MB downloads".
COMMONS_MAX_PAGE_SIZE = 25

#: How many PAGES of results one term is asked for in a single pass.
#:
#: THE REELS FEED WAS FROZEN BECAUSE OF THIS NUMBER NOT EXISTING
#: -----------------------------------------------------------
#: The search asks Commons for the NEWEST uploads first
#: (`gsrsort=create_timestamp_desc`) and the provider asked for exactly
#: one page of them. So every pass asked for the same twenty-five
#: newest files for every term, got the same twenty-five files back,
#: and stored nothing new. The Reels tab was not slow to update - it
#: was being handed the same rows it already had, and the pool never
#: grew past the newest slice of each term.
#:
#: Commons' own catalogue is far deeper than one page. Measured against
#: the live API, `intitle:nepal` holds 66 videos, `intitle:festival`
#: holds 894, and `incategory:"Videos of Snow"` holds 173. A single
#: page of 25 was never going to be the pool.
#:
#: Four pages per term is 100 candidates per term, and with a dozen
#: terms it is 1,200 candidates a pass. Most are dropped - too long, no
#: usable licence, no transcode - but the ones that survive ACCUMULATE
#: in the database, and the database is the backup pool the feed
#: rotates through.
COMMONS_PAGES_PER_TERM = 4

#: How many bands the deep paging is split into.
#:
#: The deep pages ROTATE, they do not always start at the same place.
#: Each pass always takes the newest page - that is where a new upload
#: appears - and then a band further down the results, and the band
#: moves with the hour. So over a day the pass works through the whole
#: catalogue of every term, and the pool fills without any single pass
#: having to walk the entire thing.
#:
#: Eight bands of four pages covers 200 results per term. A term with
#: fewer than that is exhausted and says so, and the pass stops early
#: rather than asking for pages that do not exist.
COMMONS_ROTATION_BANDS = 8

#: The most requests one pass may make, whatever the term list says.
#:
#: A ceiling on the burst, not on the catalogue. Commons has no quota
#: and asks clients to be polite; this keeps one pass to a few dozen
#: requests and a few tens of seconds, and the rotation fills the rest
#: over the day.
COMMONS_MAX_REQUESTS_PER_PASS = 96

#: How many search terms one pass may use.
#:
#: This was 12, and 12 was enough only when a pass asked for one page
#: of results per term. With the catalogue being walked in bands, more
#: terms means more of it is covered per pass, and the terms are cheap
#: - they are one request each, and the request budget above is what
#: actually bounds the burst.
#:
#: The number matters for coverage, not for politeness: Commons holds
#: its Nepal footage in many separate corners, and no single term
#: reaches more than a few dozen of them. Measured against the live
#: API, the terms the operator asked for hold between 1 and 66 videos
#: each - `intitle:nepal` 66, `intitle:tharu` 48, `intitle:himalayan`
#: 29, `intitle:bhaktapur` 1. The union is the pool, and the union is
#: only as big as the term list.
MAX_SEARCH_TERMS = 40

#: Floor for a "playable" rendition. Picking a 180px variant is how you
#: get a card that renders beautifully and looks like garbage.
MIN_HEIGHT = 360

#: Ceiling on the rendition we prefer to store.
#:
#: This was 720, which quietly made the feed WORSE than it was before
#: the master-file bug was fixed: Commons offers 240p / 480p / 1080p
#: transcodes plus a 1080p-or-4K master, and a 720 ceiling meant the
#: largest usable transcode was 480p. The card is rendered around
#: 420-900 CSS pixels wide and on a 3x phone screen that is 1260-2700
#: device pixels — a 480p source is visibly soft on exactly the
#: devices that can afford the bytes.
#:
#: 1080 is the ceiling because that is where Commons stops: the
#: transcode ladder tops out there, so raising it further would change
#: nothing except to make the code look like it had a higher limit
#: than it does. It also keeps the master out of the picture, which is
#: the whole point — a 4096x2160 original is tens of megabytes, and
#: the transcode at the same visual quality is a fraction of that.
PREFERRED_HEIGHT = 1080

#: The height at which the ORIGINAL file beats the transcode ladder.
#:
#: 1440 is 4K-class in the vertical sense and is what a 3x phone screen
#: can actually resolve in a 420-900px card. Below it, the master is
#: just more bytes rather than more definition.
MASTER_MIN_HEIGHT = 1440

#: The largest original file we are willing to serve.
#:
#: Measured against the live API, ordinary Commons masters of Nepal
#: clips are 159 MB, 164 MB and 806 MB. A 37 MB 4K master is a
#: reasonable thing to stream; an 807 MB one is a page that never
#: starts playing. The cap is what separates "high definition" from
#: "high definition that does not work".
MASTER_SIZE_LIMIT_BYTES = 40 * 1024 * 1024

#: Wikimedia asks every API client to identify itself. Keep this in sync
#: with the deployment's real contact details — an unidentifiable bot is
#: exactly how a project gets its API access withdrawn.
USER_AGENT = "NewsMagazineReels/1.0"

#: The words that mean a clip is about Nepal.
#:
#: The search is a constraint, not a guarantee: a file in the "Videos of
#: Mount Everest" category can be about something else entirely, and a
#: file whose title merely carries the word can be about a different
#: country. Left unchecked, the catalogue fills up with NASA timelapses
#: and Australian skydiving, and the reels feed stops being about Nepal.
#:
#: The list is deliberately generous. A false positive costs one clip
#: that is probably relevant anyway; a false negative costs a clip the
#: reader was never going to want in a Nepali news feed.
_NEPAL_TERMS = re.compile(
    r"|".join(
        (
            # The country and its people, in the spellings Commons uses.
            r"nepal|nepali|नेपाल|नेपाली",
            # The mountains and the ranges.
            r"himalaya|himalayan|everest|sagarmatha|annapurna|langtang|machhapuchhre|"
            r"manaslu|dhaulagiri|cho oyu|shisha pambma|kanchenjunga|"
            r"k2|gokyo|tillicho|khumbu|gandaki|upper gandaki|"
            r"barun|macchu|pirinchi|sugar bowl",
            # The places.
            r"kathmandu|valley|bhaktapur|patan|lalitpur|kirtipur|budhanilkantha|"
            r"changunarayan|thamel|pashupati|pashupatinath|pasupatinath|"
            r"boudha|boudhanath|swayambhu|swayambhunath|machhendranath|"
            r"pokhara|chitwan|lumbini|janakpur|bhairahawa|biratnagar|"
            r"birgunj|dharan|butwal|hetauda|banglamukhi|dhangadhi|"
            r"mustang|manang|dolpo|mugu|humla|pyan|surkhet|darchula|"
            r"bajura|dolakha|khotang|sindhuli|makalu|tehri|uttarakhand|"
            r"koshi|karnali|madhes|terai|bagmati|gandaki|"
            r"narayani|mahakali|rapti|sunkoshi|western nepal|eastern nepal|"
            r"sagarmatha zone|koshi zone|gandaki province|karnali province|"
            r"sudurpashchim|lumbini province|koshi province|madhesh|siraha|"
            r"saptari|dhanusha|mahottari|rajbiraj",
            # The peoples and the cultures.
            r"tharu|newar|newari|sherpa|tamang|limbu|magar|chepang|rai|"
            r"monastery|monasteries|gompa|stupa|chaitya|chorten|dharmachakra|"
            r"prayer flag|mandala|monk|monks|lama|"
            r"dashain|tihar|holi|chhath|bihai|magh sankranti|losar|"
            r"kathmandu durbar|patan durbar|bhaktapur durbar|"
            r"pashupati temple|muktinath|manakamana|"
            r"nagarkot|nawakot|daman|namche|namche bazaar|"
            r"pokhara valley|annapurna circuit|chitwan national park|"
            r"sagarmatha national park|maya devi|"
            r"kumbum|rastriya ban|rasa|rasoi|kukhri|sel|bhats",
        )
    ),
    re.IGNORECASE,
)


def _mentions_nepal(title: str, description: str) -> bool:
    """
    Whether this clip is about Nepal.

    The title and the description are checked TOGETHER, and either one
    answering is enough. "Landing and taking off at the most dangerous
    airport in the world" is Lukla, and its title does not say so —
    its description does. A clip whose title says "Nepal" and whose
    description is blank is still a clip about Nepal.
    """
    haystack = f"{title or ''} {description or ''}".lower()
    if not haystack.strip():
        return False
    return _NEPAL_TERMS.search(haystack) is not None


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

#: Video codecs no current browser decodes, whatever container they turn
#: up in. `theora` is Ogg Video and `mp4v` is MPEG-4 Part 2 — both are
#: valid video files, both are common on Wikimedia Commons, and neither
#: plays anywhere. This is a DISQUALIFIER, checked before the container:
#: `video/quicktime` looks like a container Safari knows and is not, and
#: `video/mp4` is a container that carries both H.264 (plays everywhere)
#: and MPEG-4 Part 2 (plays nowhere).
UNPLAYABLE_VIDEO_CODECS = frozenset({"theora", "mp4v", "mp4a"})

#: Rank 0 — mp4. H.264 in an mp4 is the one combination Safari, every
#: iOS browser and every Chromium and Firefox build will play, and it is
#: the only one all four agree on.
_RANK_MP4 = 0

#: Rank 1 — webm with VP8/VP9/AV1. Plays in Chromium and Firefox; Safari
#: on macOS has shipped VP9 since 14.1; Safari on iOS has never shipped
#: WebM at all. Good enough to store, not good enough to prefer over mp4.
_RANK_WEBM = 1


def _parse_media_type(value: Any) -> tuple[str, frozenset[str]]:
    """
    A mime type with its parameters, as (essence, codecs).

    'video/webm; codecs="vp9, opus"' -> ('video/webm', {'vp9', 'opus'})

    The parameters are the whole reason this function exists. MediaWiki
    NEVER sends a bare type in a derivative: every entry it publishes
    arrives as `video/<container>; codecs="<video>, <audio>"`, so code
    that compares the field against `"video/mp4"` with `==` is comparing
    against a string Commons has never once produced, and silently
    refuses every mp4 it is ever offered. That is not a hypothetical:
    it is what made the mp4 preference in `_best_video_url` unreachable
    code, and it is exactly the bug that left the reels feed serving
    webm. Everything downstream now reads the essence and the codecs
    separately rather than the raw field.
    """
    if not isinstance(value, str):
        return "", frozenset()

    essence, _, parameters = value.partition(";")
    essence = essence.strip().lower()
    if not essence:
        return "", frozenset()

    codecs: set[str] = set()
    for parameter in parameters.split(";"):
        key, _, raw = parameter.partition("=")
        if key.strip().lower() != "codecs":
            continue
        # Codecs are comma separated and quoted as a group; an unquoted
        # one is still a codec.
        codecs.update(
            part.strip().strip('"').lower()
            for part in raw.split(",")
            if part.strip()
        )

    return essence, frozenset(codecs)


def _video_rank(essence: str, codecs: frozenset[str]) -> int | None:
    """
    How many browsers can play this rendition, best first. None = refuse.

    Refusing rather than ranking last is deliberate and predates this
    function: Commons still hosts plenty of Ogg Video, the file is a
    perfectly valid video, and no current browser will decode it. A clip
    nobody can watch is worse than no clip — the next candidate is a
    real video.
    """
    if UNPLAYABLE_VIDEO_CODECS & codecs:
        return None

    if essence.startswith("video/mp4"):
        return _RANK_MP4

    if essence.startswith("video/webm"):
        return _RANK_WEBM

    # Anything else — `video/quicktime`, `video/ogg`, audio, an empty
    # string — is refused rather than guessed at.
    return None


def _is_browser_playable_video(url: str, info: dict | None = None) -> bool:
    """
    True when a browser will actually be able to play this file.

    Only mp4 (H.264) and webm (VP8/VP9/AV1) are treated as playable, and
    the codec is checked as well as the container: an mp4 carrying
    MPEG-4 Part 2 is a valid video file that no browser plays. The mime
    is used when the provider told us and the extension when it did not.
    """
    mime = ""
    if isinstance(info, dict):
        mime = str(info.get("mime") or info.get("type") or "")

    essence, codecs = _parse_media_type(mime)
    if essence:
        return _video_rank(essence, codecs) is not None

    lowered = url.lower().split("?")[0]
    # `.ogv` and `.ogg` are checked first: `.ogg` is audio and `.ogv` is
    # Theora video, and neither belongs in a video card.
    if lowered.endswith((".ogv", ".ogg", ".theora")):
        return False
    return lowered.endswith((".mp4", ".m4v", ".webm"))


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


#: YouTube reports a running time as an ISO-8601 duration ("PT1M23S"),
#: not a number of seconds, so the duration filter has to parse one.
#: Every other provider here sends seconds and gets to use _number().
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)


def _iso8601_duration(value: Any) -> float | None:
    """
    "PT1M23S" -> 83.0 seconds, or None when it is not a duration.

    A bare "P" or "PT" matches the pattern while carrying no time at all,
    so the zero-check is not optional: without it an empty duration would
    parse as 0.0 seconds, which reads as "instant clip" rather than
    "provider said nothing useful".
    """
    if not isinstance(value, str):
        return None

    match = _ISO_DURATION_RE.match(value.strip())
    if match is None:
        return None

    parts = match.groupdict()
    if not any(parts[key] for key in ("days", "hours", "minutes", "seconds")):
        return None

    return (
        int(parts["days"] or 0) * 86400
        + int(parts["hours"] or 0) * 3600
        + int(parts["minutes"] or 0) * 60
        + float(parts["seconds"] or 0)
    )


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

    #: Shortest interval this provider wants between passes, in seconds.
    #: Zero means "every pass".
    #:
    #: This exists because providers do not cost the same. Wikimedia and
    #: Pixabay answer cheaply and are cached on our side anyway. YouTube's
    #: search.list costs 100 units against a 10,000/day default, so a
    #: source polled on the reels cadence spends a key inside half an
    #: hour — not an outage, but a provider that silently contributes
    #: nothing after the first pass while looking configured.
    #:
    #: Declaring the cost here rather than telling the operator to raise
    #: REELS_POLL_SECONDS is the difference between a setting that has to
    #: be right and one that cannot be wrong: an expensive source is
    #: polled at its own affordable rate and the cheap ones keep the
    #: feed moving at the cadence the operator chose. The scheduler
    #: honours this.
    min_poll_seconds: int = 0

    def __init__(self, *, pool: KeyPool | None, client: httpx.AsyncClient, hint: str = "") -> None:
        self._pool = pool
        self._client = client
        self.hint = _plain_text(hint) or ""
        if self.hint:
            # A hint comes from operator config and ends up in a search
            # string; bound it so a pasted paragraph cannot become a 4KB
            # query parameter.
            #
            # 200 was sized for a list of bare place names and was a
            # silent trap once categories were allowed. `reels_search_terms`
            # is capped at 12 terms, but a `cat:Videos of Festivals` entry
            # is 23 characters, so the shipped default list came to 261 and
            # the last three terms were amputated mid-word — the hint ended
            # at "cat:Videos of Fest", which then failed the category
            # validation and was dropped. Nothing errored; the Reels tab
            # simply stopped receiving festivals, agriculture and markets
            # with no message anywhere.
            #
            # The bound has to clear the longest list `_search_terms` can
            # still act on — 40 terms at the 80-character per-category
            # limit is under 3300 — so it is set above that, and the real
            # work is still done by the term cap rather than by this slice.
            # Truncating at a comma boundary also means an over-long
            # operator value loses whole terms rather than half of one.
            if len(self.hint) > 3400:
                cut = self.hint.rfind(",", 0, 3400)
                self.hint = self.hint[: cut if cut > 0 else 3400]

    # -------------------------------------------------
    # HTTP
    # -------------------------------------------------

    def _headers(self, key: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.requires_key and key:
            headers["Authorization"] = key
        return headers

    def _scrub(self, text: str) -> str:
        """
        Remove every configured key from text that is about to be logged.

        A credential in an error message is never something to keep: those
        messages end up in SourceHealth.error, which is persisted and
        rendered in the admin panel, and in warning log lines.

        Providers that authenticate with a header are safe by
        construction. One that authenticates with `key=` in the query
        string is not — httpx.InvalidURL renders the whole url, query
        string and all, and it does NOT derive from httpx.HTTPError, so
        it sails past the handler in _send and surfaces at the boundary
        in fetch() instead. Scrubbing in both places closes both doors,
        and doing it for the whole pool rather than one key means a
        rotated-out key cannot leak either.
        """
        if self._pool is None or not text:
            return text

        for key in self._pool.keys:
            if key and key in text:
                text = text.replace(key, "***")

        return text

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

    async def _get_json(
        self,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        endpoint: str | None = None,
    ) -> dict:
        """
        One JSON GET, with key rotation.

        A 429 cools the key it was made with and tries the next one; when
        the last key is cooling, `next_key()` raises KeyPoolExhausted and
        that propagates untouched, because a full pool is the
        orchestrator's backoff decision and not a provider failure.

        `endpoint` overrides `self.endpoint` for providers that publish
        more than one API. It is threaded through to the wire rather than
        swapped by the caller, so a second call site cannot accidentally
        bypass the rotation, the Retry-After handling or the error
        boundary — all three of which are the reason this method exists.
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
            return self._decode(
                await self._send(None, params, headers, endpoint)
            )

        # Bounded so a pathological pool cannot spin. next_key() raises
        # first in practice once every key is in cooldown.
        attempts = len(pool) + 1

        for _ in range(attempts):
            key = pool.next_key()
            response = await self._send(key, params, headers, endpoint)

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
        endpoint: str | None = None,
    ) -> httpx.Response:
        merged = {"User-Agent": USER_AGENT}
        merged.update(self._headers(key))
        if headers:
            merged.update(headers)

        # A provider that puts its credential in the query string (rather
        # than in a header) overrides _send and injects it there. Copy
        # first: `params` is the caller's dict and is reused across key
        # rotation attempts, so mutating it would leak one key's value
        # into the next attempt's URL.
        url = endpoint or self.endpoint

        try:
            return await self._client.get(url, params=params, headers=merged)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"{self.key}: request failed: {self._scrub(str(exc))}"
            ) from exc

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
                f"{self.key}: {type(exc).__name__}: {self._scrub(str(exc))}"
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

    On codecs — read this before trying to fix Safari on iOS
    -------------------------------------------------------
    Commons masters are .webm and .ogv, and .ogv plays nowhere. What
    Wikimedia actually publishes, checked against the live API on a
    50-file sample (184 derivative entries) and against the CDN:

        *.vp9.webm          video/webm; codecs="vp9, opus"
        *.mpeg4.mov         video/quicktime; codecs="mp4v"   (MPEG-4 Part 2)
        *.theora.ogv        video/ogg; codecs="theora"

    There is no H.264 mp4 family. Not one of the 184 entries was
    `video/mp4`, and every `.480p.mp4` / `.480p.mpeg4.mp4` path on the
    CDN answers HTTP 404. Wikimedia stopped publishing H.264
    transcodes years ago and has not brought them back.

    The consequence, stated plainly because it is not obvious and it is
    not a bug in this file: **nothing Commons serves will play in Safari
    on iOS.** MP4 Part 2 in a QuickTime container is not decodable there
    either — Apple dropped it long ago — and WebM has never shipped on
    iOS at all. An mp4 preference below is still correct and still worth
    having; it just will not save iOS while this is the only keyless
    source. Fixing that means changing the provider, not the codec
    choice, and the read path (`isPlayableReel` in apps/web) is where
    the decision about what to show such a reader is made.

    Two things that ARE ours and were wrong:

    1. Commons lists the ORIGINAL FILE as the first entry in
       `derivatives`, at full resolution and with no `transcodekey`.
       Ranking by size therefore handed every reel a 4K master — a 4 MB
       download to fill a 420px card — while the transcodes Wikimedia
       had already paid to generate went unused. `_best_video_url` now
       skips the master and picks a transcode.

    2. Every derivative's `type` carries its codecs
       (`video/mp4; codecs="avc1..."`), so the old exact comparison
       against `"video/mp4"` could never match and the mp4 preference
       was unreachable code. `_parse_media_type` now splits the
       parameters off and reads the codecs.
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
        #
        # The ceiling is COMMONS_MAX_PAGE_SIZE rather than 50 because of
        # the trap documented on that constant: asking for 50 pages
        # *together with* `iiurlwidth` makes Commons answer HTTP 200
        # with the entire `videoinfo` module silently missing. Twelve
        # terms at 50 was therefore storing reels with no duration and,
        # worse, no `derivatives` — which is what `_best_video_url` picks
        # a transcode from, so every one of them fell back to the
        # ORIGINAL master file. Commons masters run to hundreds of
        # megabytes; the feed was pointing phones at 807MB webm files
        # and calling it a 1080p reel.
        wanted = min(COMMONS_MAX_PAGE_SIZE, max(limit, 10) * 3)

        # One query per configured term, then the union.
        #
        # A single query can only ever reach so far down Commons'
        # catalogue, and each term names a different corner of it. Twelve
        # terms searched one per hour meant the feed rotated through
        # twelve small sets instead of holding the union of them.
        #
        # Commons is a free API with no quota, so the number of requests
        # is bounded by the configured term list rather than by a budget.
        # The cap keeps a misconfigured list of 200 terms from turning
        # one poll into 200 requests.
        terms = self._search_terms()
        if not terms:
            terms = [""]

        pages: list[dict] = []
        seen_titles: set[str] = set()

        # A term that fails must not cost us the terms that worked.
        #
        # Commons is shared infrastructure and it says so: one search in
        # twelve can come back `cirrussearch-too-busy-error` — HTTP 200,
        # error in the body, "Search is currently too busy. Please try
        # again later." Before this, that raised out of `_fetch` and took
        # the entire pass with it, discarding the result sets already
        # gathered for terms one through eleven. Because it is
        # intermittent and Commons-wide, the symptom on the site is not
        # an error — it is a Reels tab that quietly stops moving while
        # the worker logs a healthy run.
        #
        # So each term is isolated. If EVERY term fails the pass still
        # raises, because "Commons returned nothing" and "Commons was
        # unavailable" are different facts and only one of them is a
        # healthy empty result.
        first_error: ProviderError | None = None
        failed_terms: list[str] = []

        # The band this pass works through.
        #
        # It moves with the hour, so consecutive passes reach further
        # down each term's results and the pool fills over the course of
        # a day rather than in one enormous pass. The term's own index
        # is mixed in so two terms do not walk the same band at the same
        # time - the requests are spread across the catalogue rather
        # than concentrated on one corner of it.
        hour = datetime.now(timezone.utc).hour
        requests_made = 0

        for index, term in enumerate(terms):
            params = {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrsearch": self._search_expression(term),
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

            # The pages this term is asked for.
            #
            # The FIRST page is the newest uploads, and it is always
            # taken: that is where genuinely new material appears, and
            # a pass that skipped it would never notice a new upload at
            # all. The rest is a rotating window further down the
            # results, which is what actually grows the pool.
            band = (hour + index) % COMMONS_ROTATION_BANDS
            offsets = [0]
            for step in range(COMMONS_PAGES_PER_TERM - 1):
                offsets.append(
                    (band * COMMONS_MAX_PAGE_SIZE) + (step * COMMONS_MAX_PAGE_SIZE)
                )

            for offset in offsets:
                if requests_made >= COMMONS_MAX_REQUESTS_PER_PASS:
                    logger.info(
                        "wikimedia: request budget for this pass reached "
                        "(%d); the remaining bands are covered by the next "
                        "pass.",
                        COMMONS_MAX_REQUESTS_PER_PASS,
                    )
                    break

                page_params = dict(params)
                if offset:
                    page_params["gsroffset"] = str(offset)
                else:
                    # Offset 0 is the default, and sending it explicitly
                    # would be noise in the request log.
                    page_params.pop("gsroffset", None)

                requests_made += 1

                try:
                    payload = await self._search_with_retry(page_params, term)
                except ProviderError as exc:
                    failed_terms.append(term)
                    if first_error is None:
                        first_error = exc
                    logger.warning(
                        "wikimedia: term %r failed at offset %d (%s); "
                        "continuing with the remaining terms.",
                        term,
                        offset,
                        str(exc)[:120],
                    )
                    break

                found = (payload.get("query") or {}).get("pages") or []
                if not isinstance(found, list):
                    break

                for page in found:
                    if not isinstance(page, dict):
                        continue
                    title = str(page.get("title") or "")
                    if title and title in seen_titles:
                        continue
                    if title:
                        seen_titles.add(title)
                    pages.append(page)

                # A short page means the term ran out. Asking for the
                # next one would be asking for nothing, so the term is
                # done.
                if len(found) < wanted:
                    break

                # Paced deliberately.
                #
                # A pass fires a burst of searches back to back.
                # CirrusSearch's answer to a burst is the "too busy"
                # error, so every request waits a moment. Half a second
                # between them is the difference between a pass that
                # usually completes and one that usually does not.
                await asyncio.sleep(SEARCH_PACING_SECONDS)

            if requests_made >= COMMONS_MAX_REQUESTS_PER_PASS:
                break

        if failed_terms and len(failed_terms) == len(terms):
            # Nothing succeeded, so this is an outage, not an empty
            # catalogue. Reporting it as an empty result is what let the
            # Reels tab go quiet with a healthy-looking log.
            raise first_error or ProviderError(
                "wikimedia: every configured term failed."
            )

        if failed_terms:
            logger.info(
                "wikimedia: %d of %d term(s) failed this pass; stored the "
                "rest rather than discarding the pass.",
                len(failed_terms),
                len(terms),
            )

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

    async def _search_with_retry(self, params: dict[str, str], term: str) -> dict:
        """
        One Commons search, retried while the answer is "busy".

        CirrusSearch reports saturation as `cirrussearch-too-busy-error`
        inside a successful HTTP 200 — a queue, not a failure, and its
        own text is "Please try again later". Riding out a busy minute
        with a short, growing pause is the difference between a pass that
        stores twelve terms' worth of new clips and one that stores none.

        Only that one code is retried. Anything else — a bad parameter, a
        DNS failure, a genuinely malformed response — will fail the same
        way three times, and retrying it would hold the scheduler slot
        (`max_instances=1`) for no gain.

        Three attempts at 2s then 4s. The pass as a whole is bounded by
        `reels_pass_timeout_seconds`, so the worst case here is a small
        fraction of that budget.
        """
        delay = BUSY_RETRY_BASE_SECONDS
        last_error: ProviderError | None = None

        for attempt in range(BUSY_RETRY_ATTEMPTS):
            try:
                payload = await self._get_json(params)
            except ProviderError as exc:
                if "too-busy" not in str(exc).lower():
                    raise
                last_error = exc
                if attempt == BUSY_RETRY_ATTEMPTS - 1:
                    break
                logger.info(
                    "wikimedia: Commons is busy for %r, retrying in %.0fs "
                    "(attempt %d/%d).",
                    term,
                    delay,
                    attempt + 1,
                    BUSY_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue

            # A page came back, but is `videoinfo` on it?
            #
            # Commons drops that module silently once a request is large
            # enough — no error, no warning, just absent data — and a
            # result without it looks perfectly healthy to every check
            # above. The consequence is not cosmetic: no `derivatives`
            # means no transcode, so `_best_video_url` falls back to the
            # original master and the feed starts serving files measured
            # in hundreds of megabytes.
            #
            # So the signature is checked rather than assumed, and the
            # page is halved and retried once. That turns a future move
            # of Wikimedia's cliff into "this pass found fewer rows"
            # instead of "the reels tab started downloading masters".
            pages = (payload.get("query") or {}).get("pages") or []
            if isinstance(pages, list) and pages and not any(
                isinstance(p, dict) and p.get("videoinfo") for p in pages
            ):
                smaller = max(10, int(params.get("gsrlimit") or 10) // 2)
                if smaller < int(params.get("gsrlimit") or 10):
                    logger.warning(
                        "wikimedia: Commons returned %d result(s) for %r with "
                        "no videoinfo at gsrlimit=%s; retrying at %s.",
                        len(pages),
                        term,
                        params.get("gsrlimit"),
                        smaller,
                    )
                    params = {**params, "gsrlimit": str(smaller)}
                    return await self._search_with_retry(params, term)

            return payload

        raise last_error or ProviderError("wikimedia: search busy.")

    def _search_terms(self) -> list[str]:
        """
        The configured terms, cleaned and bounded.

        A term has to be a plain word for `intitle:` to mean anything,
        and a term that is not plain is a search expression in its own
        right — interpolating operator syntax from configuration is how a
        search term turns into a query rewrite.

        Only two shapes are allowed, and both are ones
        `_search_expression` can actually turn into a constraint:

          - a single alphanumeric word: `nepal`
          - a space-separated phrase: `mount everest`

        Everything else is DROPPED rather than sanitised, and that
        strictness matters more than it looks. A term this method
        returns but `_search_expression` cannot use produces a query
        with no hint at all — which is the loose OR-matching search
        that put European Space Agency stock in a Nepali news feed.
        Silently widening the search is worse than not searching at all.
        """
        raw = (self.hint or "").replace("\n", ",")
        terms: list[str] = []
        seen: set[str] = set()

        for candidate in raw.split(","):
            term = candidate.strip()
            if not term:
                continue

            # A CATEGORY term, written `cat:Videos of Rain`.
            #
            # `intitle:` only reaches files whose TITLE carries the word.
            # For a subject like "rain" or "floods" almost nothing on
            # Commons is titled that way — the title is a place, a date or
            # a uploader's name — so an `intitle:`-only term list can
            # never fill a reels page. That is what left the category
            # showing the same Everest footage indefinitely: the 12-term
            # default was almost entirely Everest, Himalaya and Sagarmatha,
            # which are three names for one small pool.
            #
            # `incategory:` reaches the files a human filed as being about
            # the subject, which is the constraint actually wanted.
            # Measured against the live API, tall videos (h>=360) out of a
            # 30-result page:
            #
            #   Videos of Rain        30      Videos of Mountains      27
            #   Videos of Rivers      30      Videos of Agriculture   29
            #   Videos of Festivals   30      Videos of Cities        15
            #   Videos of Floods      30      Videos of the Himalayas   5
            #
            # against 19 for the single best title term, `intitle:nepal`.
            if term.lower().startswith("cat:"):
                name = term[4:].strip()
                # Category names legitimately contain spaces and digits.
                # What must not ride along is CirrusSearch operator
                # syntax, so this rejects anything that could close the
                # quoted string or smuggle in a second filter — the same
                # rule the phrase branch below applies to titles.
                if not name or len(name) > 80:
                    continue
                if any(ch in name for ch in '"\\^~*:<>{}()[]|'):
                    continue
                term = f"cat:{name}"
            elif term.isalnum():
                if len(term) < 3:
                    continue
            elif " " in term:
                # A phrase. Every word must be alphanumeric, so no
                # operator, wildcard or parenthesis can ride along.
                words = term.split()
                if len(words) < 2 or not all(w.isalnum() for w in words):
                    continue
                if len(term) > 60:
                    continue
            else:
                # Punctuation, or a single non-alphanumeric word.
                continue

            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)

            if len(terms) >= MAX_SEARCH_TERMS:
                break

        return terms

    # -------------------------------------------------
    # Search
    # -------------------------------------------------

    def _search_expression(self, term: str = "") -> str:
        """
        A CirrusSearch expression, not a bag of words.

        `filetype:video` keeps us out of the ogg-audio pile, and the
        non-free category is negated explicitly: those files are on
        Commons under fair-use claims, which is precisely the material
        we must never put in our own feed.

        WHY THE TERM IS SCOPED WITH `intitle:`
        ---------------------------------------
        CirrusSearch OR-matches. A bare term in the expression does not
        constrain the result set — it just adds one more way for a file
        to match, alongside `filetype:video` which matches everything.
        Measured against the live API:

          ... nepal              -> Fagu Purnima (Holi),
                                     Panche Baja (a BENGALI wedding),
                                     Landing a Lukla Airport
          ... intitle:nepal     -> Chandragiri Cable Car, Kathmandu, Nepal
                                     Dattatreya Square in the rain, Bhaktapur
                                     Nepal flood 2026 before after
                                     Trishuli River at Pida Village

        The loose search returns a Bengali wedding and a Spanish
        baseball match, because those files carry a Nepal tag somewhere
        invisible — a category, a caption, a uploader's edit. `intitle:`
        requires the term to be in the file's TITLE, which is the only
        place it reliably means "this video is about Nepal". The feed
        went from a mix of Nepali footage and European Space Agency
        stock to actually Nepali footage.

        Two `intitle:` terms in one expression AND together and match
        almost nothing, so exactly one is used per query — and the
        caller issues one query per configured term, unioning the
        results.

        A `cat:` term becomes `incategory:` instead, which is the same
        one-constraint-per-query rule pointed at the category tree rather
        than the file title. `intitle:` and `incategory:` are never
        combined: they AND together, and a file titled "Rain in Pokhara"
        that sits in "Videos of Rain" is already found by the weaker of
        the two.
        """
        parts = ["filetype:video", '-incategory:"Non-free files"']

        cleaned = (term or "").strip()
        if cleaned:
            if cleaned.lower().startswith("cat:"):
                # Quoted for the same reason a phrase is: an unquoted
                # multi-word category in CirrusSearch is OR'd, so
                # `incategory:Videos of Rain` matches every video filed
                # under "Videos", "of", or "Rain".
                parts.append(f'incategory:"{cleaned[4:].strip()}"')
            elif cleaned.isalnum() and len(cleaned) > 2:
                parts.append(f"intitle:{cleaned}")
            elif " " in cleaned and all(w.isalnum() for w in cleaned.split()):
                # A multi-word phrase. Quoted, because unquoted words in
                # CirrusSearch are OR'd and a phrase would match a file
                # with only one of them.
                parts.append(f'intitle:"{cleaned}"')
            # Anything else adds NO constraint, and a constraint-free
            # query is the loose search that started this. `_search_terms`
            # is what guarantees a term never reaches here in that
            # shape, but the belt is kept on: a term that cannot be
            # expressed must not widen the search.

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

        video_url, rendition_width, rendition_height = self._best_video_url(
            videoinfo, imageinfo
        )
        if not video_url:
            return None, "no_playable_derivative"

        original_url = _absolute((imageinfo or {}).get("url")) or _absolute(
            (videoinfo or {}).get("url")
        )

        # The rendition we actually serve, not the master it was cut from.
        # A row that says 3840x2160 while pointing a phone at a 854x480
        # transcode is a lie in a column the card renders from.
        width = rendition_width or _int_or_none((videoinfo or {}).get("width")) or _int_or_none(
            (imageinfo or {}).get("width")
        )
        height = rendition_height or _int_or_none((videoinfo or {}).get("height")) or _int_or_none(
            (imageinfo or {}).get("height")
        )

        description = (
            _plain_text(meta.get("ImageDescription"))
            or _plain_text(meta.get("ObjectName"))
        )

        # IS THIS ABOUT NEPAL?
        # ----------------------
        # The search is a constraint, not a guarantee. A file in the
        # "Videos of Mount Everest" category can be about something
        # else entirely, and a file whose title merely carries the word
        # can be about a different country. The categories hold enough
        # incidental material that a feed built straight from them
        # fills up with NASA timelapses and Australian skydiving.
        #
        # So the clip is checked against the subject before it is
        # stored. A clip with no Nepal signal in its title OR its
        # description is dropped.
        #
        # "OR" rather than "AND" on purpose: "Landing and taking off
        # at the most dangerous airport in the world" is Lukla, and
        # its title does not say so - its description does. And a
        # clip whose title says "Nepal" and whose description is blank
        # is still a clip about Nepal.
        if not _mentions_nepal(file_name, description):
            return None, "off_topic"

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

    def _best_video_url(
        self, videoinfo: dict | None, imageinfo: dict | None
    ) -> tuple[str | None, int | None, int | None]:
        """
        Prefer an mp4 transcode, then a webm transcode, then the master.

        Returns (url, width, height) for the rendition actually chosen,
        so the row describes the file the browser will fetch rather than
        the master it was derived from.

        Commons publishes three transcode families and nothing else:
        `*.vp9.webm`, `*.mpeg4.mov` (MPEG-4 Part 2) and `*.theora.ogv`.
        It publishes no H.264 mp4 at all — verified against the live API
        and against the CDN, where every `.480p.mp4` path 404s. So the mp4
        branch below is not dead code, it is a preference that costs
        nothing and is right the day Wikimedia turns transcoding back on
        or a different provider arrives; what it is NOT is a fix for
        Safari on iOS. That platform cannot play anything Commons serves,
        which is a property of the provider, not of this code. Say so in
        the README before trying to fix it here again.

        What this DOES fix is the master. Commons lists the original file
        as the FIRST entry in `derivatives`, with no `transcodekey` and
        at full resolution, so ranking by size handed every reel a 4K
        master — a 4 MB download to fill a 420px card — and left the
        transcodes Commons had already paid to generate unused.
        """
        candidates: list[tuple[int, int, int, str]] = []
        master_urls = {
            self._url_path(_absolute((block or {}).get("url")))
            for block in (videoinfo, imageinfo)
            if (block or {}).get("url")
        }

        for derivative in (videoinfo or {}).get("derivatives") or []:
            if not isinstance(derivative, dict):
                continue
            url = _absolute(derivative.get("src"))
            if not url or not self._is_allowed_host(url):
                continue

            # The master, however it is flagged. `transcodekey` is absent
            # on every genuine transcode and present on every one of them
            # in the live API (134 of 184 derivative entries across a
            # 50-file sample — the other 50 are one master per file), and
            # the URL comparison catches it if that ever stops holding.
            if not derivative.get("transcodekey") or self._url_path(url) in master_urls:
                continue

            rank = _video_rank(*_parse_media_type(derivative.get("type")))
            if rank is None:
                continue

            candidates.append((
                rank,
                _int_or_none(derivative.get("height")) or 0,
                _int_or_none(derivative.get("width")) or 0,
                url,
            ))

        if candidates:
            best_rank = min(item[0] for item in candidates)
            pool = [item for item in candidates if item[0] == best_rank]

            # A rendition below MIN_HEIGHT is a thumbnail and looks like
            # one, but it still beats a 4K master: the card is 420px wide.
            usable = [item for item in pool if item[1] >= MIN_HEIGHT] or pool

            # Largest that does not exceed PREFERRED_HEIGHT, or the
            # smallest that does. Downloading 1080p to fill a 420px card
            # is how a reels feed becomes the most expensive page on the
            # site.
            below = [item for item in usable if item[1] <= PREFERRED_HEIGHT]
            chosen = (max if below else min)(
                below or usable, key=lambda item: (item[1], item[2])
            )
            return chosen[3], chosen[2] or None, chosen[1] or None

        # No usable transcode, or the transcode ladder tops out below
        # the quality we want. Consider the MASTER.
        #
        # The master is normally a trap: Commons files are enormous.
        # Measured against the live API, the masters of ordinary Nepal
        # clips are 159 MB, 164 MB, and — for one festival video —
        # 806 MB, all of them 1920x1080. Serving an 807 MB file to
        # fill a 420px card is not high definition, it is a broken feed.
        #
        # But some masters ARE genuinely higher than the transcode
        # ladder: "Chandragiri Cable Car, Kathmandu, Nepal" is
        # 3840x2160 at 37.5 MB, and the ladder only reaches 1080p. For
        # those the master is the better file by a wide margin, and it
        # is affordable.
        #
        # So the rule is: take the master when it is at least
        # MASTER_MIN_HEIGHT tall AND no bigger than
        # MASTER_SIZE_LIMIT_BYTES. Below that height it is not more
        # definition, it is just more bytes. Above the size limit it is
        # not high definition either, it is a download.
        for block in (videoinfo, imageinfo):
            url = _absolute((block or {}).get("url"))
            if not url or not self._is_allowed_host(url):
                continue
            if not _is_browser_playable_video(url, block):
                continue

            height = _int_or_none((block or {}).get("height")) or 0
            size = _int_or_none((block or {}).get("size")) or 0

            if height >= MASTER_MIN_HEIGHT and 0 < size <= MASTER_SIZE_LIMIT_BYTES:
                return (
                    url,
                    _int_or_none((block or {}).get("width")) or None,
                    height or None,
                )

            # The master exists but is not worth it: too small to be an
            # upgrade, or too large to serve. Fall through to the
            # transcode path below.

        # Last resort: the master, whatever it is, as long as a browser
        # can actually play it. A file with no transcode has no
        # alternative, and a large file that plays beats a card that
        # does not.
        for block in (videoinfo, imageinfo):
            url = _absolute((block or {}).get("url"))
            if not url or not self._is_allowed_host(url):
                continue
            if _is_browser_playable_video(url, block):
                return (
                    url,
                    _int_or_none((block or {}).get("width")) or None,
                    _int_or_none((block or {}).get("height")) or None,
                )

        return None, None, None

    @staticmethod
    def _url_path(url: str | None) -> str:
        """The path, with Wikimedia's analytics query string removed."""
        return urlparse(url).path if url else ""

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

    # Pixabay's terms require a 24-hour cache and say plainly: "the API
    # is made for real human requests; do not send lots of automated
    # queries."
    #
    # The reels job runs every five minutes, so without this it would
    # issue 288 identical requests a day per search term — the exact
    # pattern that clause exists to prevent, and it would spend a
    # free-tier quota that is keyed per API key rather than per IP.
    CACHE_TTL_SECONDS = 24 * 60 * 60

    # Query fingerprint -> (fetched_at, payload). Module-level so the
    # cache survives an adapter being rebuilt on every pass, and bounded
    # so a rotating set of search terms cannot grow it for ever.
    _cache: dict[str, tuple[float, dict]] = {}
    _CACHE_MAX_ENTRIES = 64

    async def _get_json_cached(self, params: dict[str, Any]) -> dict:
        """
        `_get_json`, memoised for the 24 hours Pixabay's terms require.

        Pixabay: "To keep the Pixabay API fast for everyone, requests
        must be cached for 24 hours. Also, the API is made for real
        human requests; do not send lots of automated queries."

        Both halves of that are load-bearing. The reels pass runs every
        five minutes against one search term, so an uncached adapter
        issues 288 byte-identical requests a day — precisely the
        automated-query pattern the clause forbids, against a free tier
        whose limit is counted per API key and not per IP, so hitting it
        costs the operator a key rather than costing us a request.

        The fingerprint deliberately EXCLUDES the key. Rotating keys is
        how the pool spreads quota, and a cache keyed per key would
        multiply every entry by the pool size — which is the opposite of
        the point. Pixabay's result does not vary by which of our keys
        asked for it.

        Empty responses ARE cached, deliberately. An empty body is a
        real answer to a real query, and re-asking it every five minutes
        is precisely the traffic the 24-hour rule exists to prevent. The
        cost of that choice is real and worth stating: one bad minute
        costs that search term a day, and the fix is a wider term, not a
        shorter TTL.

        Failures are a different thing. They raise out of `_get_json`
        before this method ever stores anything, so an upstream error can
        never be cached — a transient 500 costs one pass, not 288.
        """
        fingerprint = hashlib.sha256(
            "&".join(f"{key}={params[key]}" for key in sorted(params)).encode()
        ).hexdigest()

        now = time.monotonic()
        hit = self._cache.get(fingerprint)
        if hit is not None:
            fetched_at, payload = hit
            age = now - fetched_at
            if age < self.CACHE_TTL_SECONDS:
                logger.debug(
                    "pixabay: cache hit for %s (%.1fh old of %.1fh), not re-requesting.",
                    params.get("q"),
                    age / 3600,
                    self.CACHE_TTL_SECONDS / 3600,
                )
                return payload
            # Expired. Dropped before the refetch so a failure cannot
            # leave a stale entry behind pretending to be fresh.
            del self._cache[fingerprint]

        payload = await self._get_json(params)

        # Evict the oldest insertions first. dict preserves order, and
        # every entry is written once and left alone, so the first key
        # is the oldest — no sorting needed.
        while len(self._cache) >= self._CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)), None)

        self._cache[fingerprint] = (now, payload)
        return payload

    async def _fetch(self, limit: int) -> list[Reel]:
        params = {
            "q": self.hint or "vertical",
            "per_page": str(min(200, max(limit, 10) * 3)),
            "safesearch": "true",
            # Pixabay answers in XML unless asked for JSON; this is the
            # documented switch and it is not optional.
            "json": "1",
        }

        payload = await self._get_json_cached(params)

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
# YOUTUBE SHORTS
# ---------------------------------------------------------------------------

class YouTubeShortsSource(_BaseSource):
    """
    YouTube Shorts, embedded through YouTube's own player.

    This adapter is categorically different from the three above, and the
    difference is the whole reason it is documented at length.

    What it is NOT
    --------------
    It is not a redistributable stock library. Pexels, Pixabay and
    Commons all grant US permission to republish their clips as our own
    feed items. YouTube grants nothing of the kind: the YouTube Standard
    Licence is a licence the UPLOADER holds over their own video, and it
    confers no republication right on a third party at all. Treating this
    adapter like its neighbours — "a source whose clips we may show" —
    would be a straight misreading of what the API returns.

    What it is
    ----------
    An EMBEDDING source. Every row points at YouTube's official embed
    player, which is the sharing mechanism YouTube itself provides and
    documents. We show the video inside YouTube's own player, credit the
    channel, and link to the channel. We do not download it, we do not
    re-host it, and we do not claim it.

    Why `video_url` is an embed URL and NEVER a media URL
    ------------------------------------------------------
    The playable bytes behind a YouTube video live on googlevideo.com and
    are served from URLs carrying a signature that must be derived from
    the client. Extracting one means reproducing YouTube's player
    obfuscation — scraping, in every sense that matters — and it breaks
    the moment YouTube changes the scheme, which it does without notice.

    So this adapter does not attempt it, at all, ever. `video_url` is
    `https://www.youtube.com/embed/<id>`: the official iframe player
    src. That URL plays, it is stable, and using it is the sanctioned
    path. A row whose video_url is a googlevideo.com URL could only have
    come from somewhere that decoded a signature, and that is a bug.

    Consequence for the web player
    ------------------------------
    An embed URL is an HTML document, not a media file, so it does NOT
    play in a `<video src>` — the browser rejects it and the card renders
    "Replay unavailable". Every row from this adapter therefore also sets
    `embed_url`, which is what tells ReelCard to render an <iframe>
    instead. See `Reel.embed_url` and migration 0015.

    What `player` would have given us, and why it is not called
    ----------------------------------------------------------
    The `player` part returns the direct streaming formats, and it is
    the only place the real media URL exists. It REQUIRES OAuth: an
    API key returns HTTP 401 for it, always. This adapter uses an API
    key and must never grow an OAuth token, an `Authorization: Bearer`,
    or a `youtubeAnalytics` scope — there is no configured client secret
    to make one with, and adding one would mean storing a user credential
    to serve a feed that embeds perfectly well without it.

    Quota — read this before enabling it on a 5-minute schedule
    ----------------------------------------------------------
    `search.list` costs 100 units per call and `videos.list` costs 1.
    Google's default daily quota is 10,000 units, which is roughly 95
    searches. REELS_POLL_SECONDS defaults to 300, i.e. 288 passes a day,
    so an unconfigured-for-this YouTube provider will exhaust its quota
    within half an hour and then 429 for the rest of the day. That is
    survivable — the pool cools the key and the other providers carry on
    — but it means YouTube adds nothing unless REELS_POLL_SECONDS is
    raised to an hour or two, or several keys are configured.
    """

    key = "youtube"
    requires_key = True
    provider_name = "YouTube"
    endpoint = "https://www.googleapis.com/youtube/v3/search"

    #: search.list and videos.list are one host and one key but two
    #: paths, so the second call goes through _get_json(endpoint=...).
    videos_endpoint = "https://www.googleapis.com/youtube/v3/videos"

    # search.list is 100 units and the default daily allowance is
    # 10,000. One pass is 100, so 100 passes is the whole day's budget
    # — and at the reels cadence of every five minutes there are 288.
    #
    # The provider used to be told about this in a docstring and left to
    # waste its key. Declaring it here instead means a deployment that
    # adds YOUTUBE_API_KEYS and nothing else gets a working feed: the
    # scheduler polls this hourly, spends ~2,400 units a day, and the
    # other providers keep their five-minute cadence. Raising
    # REELS_POLL_SECONDS globally is the other way to solve it, and it
    # is the worse one — it also slows down the sources that cost
    # nothing.
    min_poll_seconds = 3600

    #: The official embed player. `video_url` and `embed_url` are both
    #: this, which is why a bare <video src> cannot play it.
    _EMBED_BASE = "https://www.youtube.com/embed/"

    #: The licence most uploads carry: the uploader keeps their rights
    #: and grants nothing to us beyond embedding.
    LICENSE_STANDARD = "YouTube Standard License"
    LICENSE_STANDARD_URL = (
        "https://support.google.com/youtube/answer/2797468"
    )

    #: A genuinely permissive one. CC BY 3.0 is a real Creative Commons
    #: licence the uploader chose, so this is the case where we can say
    #: what the terms actually are instead of describing a permission
    #: that was never extended to us.
    LICENSE_CC = "CC BY 3.0"
    LICENSE_CC_URL = "https://creativecommons.org/licenses/by/3.0/"

    #: search.list caps maxResults at 50, and videos.list accepts at
    #: most 50 ids per call — the same number, so one videos.list always
    #: covers everything search returned and no chunking is needed.
    _MAX_RESULTS = 50

    # -------------------------------------------------
    # Credentials
    # -------------------------------------------------

    def _headers(self, key: str | None) -> dict[str, str]:
        """
        No Authorization header, deliberately.

        The base class puts the key in `Authorization`, which is correct
        for Pexels and wrong for Google: that header is reserved for
        OAuth bearer tokens, and a bare API key sent there is either
        ignored or rejected. Google's own parameter is `key=` in the
        query string.
        """
        return {"Accept": "application/json"}

    async def _send(self, key, params, headers, endpoint=None):
        """
        Add `key=` to the query string, then let the base do the rest.

        `params` is copied rather than mutated: _get_json reuses the same
        dict across every key-rotation attempt, and writing the first
        key into it would leave that key sitting in the URL the second
        attempt sends — a credential in a log line, from our own pool.
        """
        query = dict(params)
        if key:
            query["key"] = key
        return await super()._send(key, query, headers, endpoint)

    # -------------------------------------------------
    # Fetch
    # -------------------------------------------------

    async def _fetch(self, limit: int) -> list[Reel]:
        # Over-fetch, for the same reason Wikimedia does. Most hits are
        # dropped for being long, for not being embeddable or for being
        # live, and asking for exactly `limit` would return a short feed.
        wanted = min(self._MAX_RESULTS, max(limit, 10) * 2)

        hits = await self._search(wanted)
        if not hits:
            return []

        details = await self._details([hit["id"] for hit in hits])

        reels: list[Reel] = []
        skipped_too_long = 0
        skipped_not_embeddable = 0
        skipped_live = 0
        skipped_no_licence = 0

        for hit in hits:
            detail = details.get(hit["id"])
            if detail is None:
                # Deleted, private, or made non-embeddable between the
                # two calls. videoEmbedDeny=FAIL already asked search to
                # exclude the unembeddable ones; a miss here is the
                # residual, and there is nothing honest to show for it.
                continue

            reel, reason = self._to_reel(hit, detail)

            if reel is not None:
                reels.append(reel)
            elif reason == "too_long":
                skipped_too_long += 1
            elif reason == "not_embeddable":
                skipped_not_embeddable += 1
            elif reason == "live":
                skipped_live += 1
            elif reason == "no_licence":
                skipped_no_licence += 1

        if skipped_too_long or skipped_not_embeddable or skipped_live or skipped_no_licence:
            logger.debug(
                "youtube: dropped %d too long, %d not embeddable, %d live "
                "and %d with no permissive licence.",
                skipped_too_long, skipped_not_embeddable, skipped_live,
                skipped_no_licence,
            )

        return _sorted_by_timestamp(reels)

    async def _search(self, wanted: int) -> list[dict]:
        """Video hits from search.list, reduced to id + snippet."""
        params = {
            "part": "snippet",
            "type": "video",
            "q": self.hint or "shorts",
            "maxResults": str(wanted),
            # Newest first, so a repeated pass finds material it has not
            # already stored. Relevance ordering would re-serve the same
            # top videos on every pass and the feed would never move.
            "order": "date",
            # YouTube's own "short" bucket. See the class docstring for
            # the two things this does not do: it is a coarse server-side
            # prefilter, and it excludes genuine Shorts under 60s.
            "videoDuration": "short",
            # The single most important filter in this adapter. FAIL
            # means "exclude videos whose owner forbids embedding", so
            # every hit that survives has already told us the uploader
            # permits a player on another site.
            "videoEmbedDeny": "FAIL",
            # Keeps a Reels feed on a news site from becoming whatever
            # else a keyword search turns up.
            "safeSearch": "strict",
        }

        payload = await self._get_json(params)

        items = payload.get("items")
        if not isinstance(items, list):
            return []

        hits: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            if isinstance(video_id, dict):
                video_id = video_id.get("videoId")
            # The API returns id objects of several shapes depending on
            # what `type` matched; only the video one is usable here.
            if not isinstance(video_id, str) or not video_id.strip():
                continue
            hits.append(
                {
                    "id": video_id.strip(),
                    "snippet": item.get("snippet")
                    if isinstance(item.get("snippet"), dict)
                    else {},
                }
            )

        return hits

    async def _details(self, ids: list[str]) -> dict[str, dict]:
        """
        The authoritative facts for a set of ids, keyed by id.

        `contentDetails.duration` is the only trustworthy length we can
        get, and `status.embeddable` is the only trustworthy answer to
        "may we put a player on this page". Both are in `videos.list`;
        neither is in `search.list`, which is why this call exists at all.
        """
        if not ids:
            return {}

        params = {
            "part": "contentDetails,status,snippet",
            "id": ",".join(ids[: self._MAX_RESULTS]),
            "maxResults": str(min(len(ids), self._MAX_RESULTS)),
        }

        payload = await self._get_json(params, endpoint=self.videos_endpoint)

        items = payload.get("items")
        if not isinstance(items, list):
            return {}

        found: dict[str, dict] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            if isinstance(video_id, str) and video_id:
                found[video_id] = item

        return found

    # -------------------------------------------------
    # Parsing
    # -------------------------------------------------

    def _to_reel(self, hit: dict, detail: dict) -> tuple[Reel | None, str | None]:
        """One video -> one Reel, or the reason it was dropped."""
        video_id = hit["id"]

        # The search snippet is preferred for the human fields because
        # it is the same object the ordering came from, but either is
        # fine — they are the same record from two calls.
        snippet = hit.get("snippet") or {}
        if not isinstance(snippet, dict):
            snippet = {}
        detail_snippet = detail.get("snippet") if isinstance(detail.get("snippet"), dict) else {}
        merged = {**detail_snippet, **snippet}

        status = detail.get("status") if isinstance(detail.get("status"), dict) else {}

        # Embedding is a permission, and the search filter only believed
        # the index. videos.list is the record of truth, and an absent
        # `embeddable` is a NO: defaulting it to True would let exactly
        # the videos the owner locked down into the feed.
        if status.get("embeddable") is not True:
            return None, "not_embeddable"

        if status.get("privacyStatus") == "private":
            return None, "not_embeddable"

        # A live or scheduled broadcast is not a Short. `none` is the
        # only value that means "an ordinary uploaded video".
        broadcast = str(merged.get("liveBroadcastContent") or "").lower()
        if broadcast and broadcast != "none":
            return None, "live"

        content_details = (
            detail.get("contentDetails")
            if isinstance(detail.get("contentDetails"), dict)
            else {}
        )

        duration = _iso8601_duration(content_details.get("duration"))
        if duration is None or duration <= 0:
            # Unlike the other providers, an unknown length here is a
            # REJECT rather than a pass. _within_duration() lets None
            # through because Commons clips often have no running time
            # and are still fine; a YouTube video with no parseable
            # duration is a record we could not verify is a Short, and
            # "we could not verify" is not a licence to publish.
            return None, "too_long"
        if duration > MAX_SECONDS:
            return None, "too_long"

        licence = self._licence(status)
        if licence is None:
            return None, "no_licence"
        license_name, license_url = licence

        creator_name, creator_url = self._creator(merged)

        title = _plain_text(merged.get("title"))
        if not title:
            # Never emit an untitled, unattributed card. The channel is
            # still known, so the reel is real — but a row whose only
            # text is a channel name is not what a feed wants, and the
            # caller can always fetch a wider search.
            title = None

        embed_url = self._EMBED_BASE + video_id

        return (
            Reel(
                source_key=self.key,
                external_id=video_id,
                # An embed player src, deliberately. See the class
                # docstring: the raw googlevideo stream needs signature
                # decoding and is never produced here.
                video_url=embed_url,
                embed_url=embed_url,
                poster_url=self._poster(merged, video_id),
                title=title,
                description=_plain_text(merged.get("description")),
                creator_name=creator_name,
                creator_url=creator_url,
                license_name=license_name,
                license_url=license_url,
                # True unconditionally, including for the CC rows. A
                # uploader who picked CC BY 3.0 is owed the credit, and
                # the class docstring explains why nothing here is ever
                # "attribution not required".
                attribution_required=True,
                # YouTube does not report the dimensions of a Short, and
                # guessing 1080x1920 from the format would be inventing
                # data. None renders as an unknown aspect ratio, which
                # the feed already handles.
                width=None,
                height=None,
                duration_seconds=duration,
                published_at=_iso(merged.get("publishedAt")),
            ),
            None,
        )

    def _licence(self, status: dict) -> tuple[str, str] | None:
        """
        (license_name, license_url) — or None when we cannot read one.

        `creativeCommon` means the uploader licensed the video CC BY 3.0,
        which is a real, named, permissive licence. `youtube` is the
        platform default: it says the uploader keeps their rights, and it
        extends nothing to a third party that embeds it.

        Anything else is refused rather than guessed. YouTube has shipped
        licence values this adapter has never seen, and defaulting an
        unrecognised one to "standard" would publish a row whose licence
        we had not actually read.

        The spellings are the documented ones — `creativeCommon` and
        `youtube` — lowercased before comparison because the API has
        returned this field with inconsistent casing, and a licence that
        is dropped for a capital C would silently empty the feed's best
        material.
        """
        licence = str(status.get("license") or "").strip().lower()

        if licence == "youtube":
            return self.LICENSE_STANDARD, self.LICENSE_STANDARD_URL

        # `status.license` is documented as one of "youtube" or
        # "creativeCommon". `creativeCommon` is the value the API
        # actually sends; the long form is accepted too so a future
        # rename is a licence that keeps working rather than one that
        # silently empties the feed of its best-attributed material.
        if licence in {"creativecommons", "creativecommon"}:
            return self.LICENSE_CC, self.LICENSE_CC_URL

        return None

    def _creator(self, snippet: dict) -> tuple[str, str | None]:
        """
        The channel, which is the only creator YouTube exposes.

        `channelId` is authoritative and stable; `channelTitle` is the
        display name and can be renamed out from under a stored row,
        which is another reason to prefer the id for the link.

        The channel title is mandatory. This adapter falls back to the
        provider name rather than emitting None, because a card that
        credits nobody is precisely the thing the whole package is
        written to prevent — and on YouTube, a channel is always present
        on a public video, so a missing title means the API shape moved.
        """
        name = _plain_text(snippet.get("channelTitle")) or self.provider_name

        channel_id = snippet.get("channelId")
        if isinstance(channel_id, str) and channel_id.strip():
            return name, f"https://www.youtube.com/channel/{channel_id.strip()}"

        return name, "https://www.youtube.com/"

    def _poster(self, snippet: dict, video_id: str) -> str | None:
        """
        YouTube's own thumbnail for the video.

        The API's `high` thumbnail is `hqdefault.jpg`, which is
        guaranteed to exist for every video and is why it is used rather
        than the 1280px `maxresdefault` — that one is absent on many
        uploads and would 404 for a good share of the feed. It is 4:3
        and letterboxed rather than vertical, so the card crops it; a
        guaranteed image that crops beats a perfect one that is not
        there.
        """
        thumbnails = (
            snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        )

        for bucket in ("high", "medium", "default"):
            entry = thumbnails.get(bucket)
            if isinstance(entry, dict):
                url = _absolute(entry.get("url"))
                if url:
                    return url

        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, type] = {
    "wikimedia": WikimediaSource,
    "pexels": PexelsSource,
    "pixabay": PixabaySource,
    "youtube": YouTubeShortsSource,
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