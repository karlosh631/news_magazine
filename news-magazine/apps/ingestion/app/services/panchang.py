"""
The daily Nepali almanac: Bikram Sambat date, tithi, nakshatra,
sunrise/sunset and the auspicious/inauspicious time windows.

WHAT THIS IS
------------
The header of a Nepali news site is expected to carry today's
panchang. This module fetches it and writes one row per day to
`daily_panchang` (migration 0022_daily_panchang.sql).

It is deliberately NOT part of the article pipeline. An almanac is not
an article: it is not normalised, not validated, not summarised, and
not deduplicated against anything. It has its own table, its own
service and its own job, and nothing it does can affect the outlets
that are being polled twenty times an hour.

WHERE THE DATA COMES FROM
--------------------------
hamropatro.com, which publishes the Nepali panchang every day and
credits the Nepal Panchanga Nirnayak Samiti at the foot of the page.

robots.txt, read before any of this was written:

    User-Agent: *
    Disallow: /widgets/
    Disallow: /login
    Disallow: /en/login
    Disallow: /contest-term-condition
    Disallow: /terms
    Disallow: /privacy
    Disallow: /bookmark
    Disallow: /notes

It is a blacklist, and neither `/rashifal` nor `/date/` is on it. Both
are allowed. Nothing here touches a disallowed path.

THE TWO PAGES, AND WHY THERE ARE TWO
------------------------------------
1. `/rashifal` — the twelve-sign rashifal. It carries the site's
   "today" block, which is where the current Bikram Sambat date comes
   from, AND the twelve horoscopes themselves. It is a small page and
   it is fetched on every pass.

2. `/date/{bs-year}-{bs-month}-{bs-day}` — the per-day page, and the
   one that actually carries the panchang: tithi, nakshatra, yoga,
   karana, sunrise, sunset, and the two फल tables that say which
   hours of the day and the night are auspicious and which are not.

TWO REQUESTS, NOT ONE, AND THE SECOND ONE IS EXPENSIVE
-------------------------------------------------------
The per-day page is addressed by BIKRAM SAMBAT date, and the site's
own BS/AD conversion is the authority — BS calendar quirks (the
2033-2035 corrections, for one) are not something to reimplement
here. So the day has to be resolved before it can be fetched, and
that costs the first request.

The second page is a big one, and it is fetched only when the day has
actually rolled over. `_last_stored_bs` remembers the BS date this
process last wrote, so a job that runs hourly fetches the small page
every hour and the large page once a day.

THE TWELVE HOROSCOPES RIDE ALONG
--------------------------------
The first request carries the twelve signs as well as the date, so
the horoscopes cost NO extra request at all: they are parsed out of
a page this module was fetching anyway. They are stored in their own
table (`daily_rashifal`, twelve rows a day) and their write is
guarded separately, so a failure in one cannot stop the other. See
app/services/rashifal.py.

`/date/` IS ALSO THE ARCHIVE
----------------------------
The per-day path is a real archive, not a rolling "today" view:
`/date/2083-10-5` is Magh 5, 2083 and it will still say Magh 5,
2083 tomorrow. So a day can be backfilled by asking for its BS date,
which is how a history gets built rather than only ever today.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.services.ingestion import db_call

logger = logging.getLogger("panchang")


# =============================================================
# THE SOURCE
# =============================================================

#: The site. Every request in this module goes to this host and no
#: other.
PANCHANG_SOURCE = "https://hamropatro.com"

#: The page that carries the current Bikram Sambat date.
RASHIFAL_PATH = "/rashifal"

#: The per-day page, addressed by Bikram Sambat date. The month is
#: 1-based (Baishakh = 1) because that is how the site numbers them.
DATE_PATH_TEMPLATE = "/date/{year}-{month}-{day}"

#: The migration that creates `daily_panchang`. Named in log messages
#: on purpose: "something is wrong" is useless at 3am, "apply
#: 0022_daily_panchang.sql" is a thing the operator can do.
PANCHANG_MIGRATION = "0022_daily_panchang.sql"

#: The upsert key. The Gregorian day the row is FOR — never the BS
#: date, which is what the source is addressed by but not what the
#: site reads.
PANCHANG_CONFLICT = "panchang_date"

#: Nepal Time: UTC+05:45, with no daylight saving since 1986. A
#: fixed offset is therefore exact for every date this pipeline will
#: ever handle, and it removes a dependency on a timezone database
#: being present on the host.
NEPAL_UTC_OFFSET = timedelta(hours=5, minutes=45)


class PanchangError(Exception):
    """Anything that goes wrong fetching or parsing the almanac."""


class PanchangParseError(PanchangError):
    """The response was not what the parser expected."""


# =============================================================
# PARSING HELPERS
# =============================================================

#: Devanagari digits to ASCII. The site prints its times and its
#: dates in Nepali numerals, and "६:०७" has to become "6:07".
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

#: The bootstrap block. The site is a Next.js app that server-renders
#: its data into a single JSON island rather than fetching it, so
#: this block is the machine-readable form of the page — and is
#: preferred over scraping the visible text wherever the two overlap.
_BOOTSTRAP_RE = re.compile(
    r'<script[^>]*id="hp-bootstrap"[^>]*type="application/json"[^>]*>'
    r"(?P<payload>.*?)</script>",
    re.DOTALL,
)

#: A clock time in a फल cell: "बिहानको ६:०७", "दिउँसोको १२:१४".
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:.]\s*(\d{2})")

#: The two फल tables, by their heading.
_PHALA_TABLES = {
    "दिन फल": "day",
    "रात्रि फल": "night",
}

#: शुक्ल (waxing) and कृष्ण (waning), by the index the site sends.
PAKSHA_NAMES = {0: "शुक्ल", 1: "कृष्ण"}

#: The फल that mean "auspicious". Everything else the site prints is
#: inauspicious: रोग (disease), उद्वेग (agitation), चर (wandering)
#: and काल (an ill hour) are the four the panchang counts against
#: the day, and लाभ (gain), अमृत (nectar) and शुभ (good) are the
#: three it counts for it.
AUSPICIOUS_RESULTS = frozenset({"शुभ", "लाभ", "अमृत"})

#: Minutes in a day. The night windows run past midnight, so their
#: end can be greater than this.
_MINUTES_PER_DAY = 1440


def _to_ascii_digits(text: str) -> str:
    """Replace Devanagari numerals with ASCII ones."""
    return (text or "").translate(_DEVANAGARI_DIGITS)


def extract_bootstrap(html: str) -> dict[str, Any]:
    """
    The page's JSON island, as a dict.

    This is the machine-readable form of the page and the thing this
    module parses wherever it can: the visible text and this JSON are
    two renderings of the same numbers, and the JSON does not move
    when the site's CSS does.
    """
    match = _BOOTSTRAP_RE.search(html or "")
    if not match:
        raise PanchangParseError(
            "the page carries no hp-bootstrap JSON block"
        )

    try:
        payload = json.loads(match.group("payload"))
    except (ValueError, TypeError) as exc:
        raise PanchangParseError(
            f"the hp-bootstrap block is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise PanchangParseError("the hp-bootstrap block is not an object")

    return payload


def _clock_minutes(text: str) -> int | None:
    """
    The 12-hour clock value of a फल cell, in minutes after midnight.

    "रातको ७:०८" -> 428. The 12-hour value is deliberately NOT the
    24-hour one: whether "१२:१४" is noon or half past midnight is
    decided by the word in front of it and by where the cell falls in
    the day's sequence, and that is settled in `_unwrap_times`.
    """
    match = _CLOCK_RE.search(_to_ascii_digits(text or ""))
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if not 1 <= hour <= 12 or minute > 59:
        return None

    return (hour % 12) * 60 + minute


def _time_candidates(value: int, ceiling: int) -> list[int]:
    """
    Every 24-hour reading a 12-hour clock value could have.

    A value can be on the hour it reads (AM) or twelve hours later
    (PM), and either can be on the day the sequence started or any
    day after it. The caller picks the one that fits.

    The candidate set deliberately reaches two days past the ceiling.
    The night's last window ends at TOMORROW's sunrise, and the site
    computes that separately from today's — on the day this was
    written it was 06:08 against today's 06:07, and a candidate set
    that stopped at today's sunrise could not hold it.
    """
    candidates: set[int] = set()
    limit = ceiling + 2 * _MINUTES_PER_DAY
    for base in (value, value + 720):
        day = 0
        while base + _MINUTES_PER_DAY * day <= limit:
            candidates.add(base + _MINUTES_PER_DAY * day)
            day += 1
    return sorted(candidates)


def _unwrap_times(
    values: list[int | None], floor: int, ceiling: int
) -> list[int | None]:
    """
    Turn a run of 12-hour clock values into minutes from local
    midnight, continuing past midnight where the day does.

    The day's windows run sunrise -> sunset and the night's run
    sunset -> the next sunrise, so the night sequence crosses
    midnight. "रातको १२:१४" is half past midnight at one end of the
    night and "रातको ७:०८" is eight in the evening at the other, and
    the word in front of the number does not tell them apart.

    The sequence does. Every reading must be at or after the one
    before it and inside the period it belongs to, and the reading
    that fits is always the smallest such one.
    """
    resolved: list[int | None] = []
    previous = floor

    for value in values:
        if value is None:
            resolved.append(None)
            continue

        options = [
            candidate
            for candidate in _time_candidates(value, ceiling)
            if candidate >= previous
        ]
        if not options:
            options = [
                candidate
                for candidate in _time_candidates(value, ceiling)
                if candidate >= floor
            ] or _time_candidates(value, ceiling)

        best = min(options)
        resolved.append(best)
        previous = best

    return resolved


def _hhmm(minutes: int) -> str:
    """Minutes from local midnight as "HH:MM", wrapping at a day."""
    wrapped = minutes % _MINUTES_PER_DAY
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def _minutes_from_seconds(value: Any) -> int | None:
    """The site's seconds-after-midnight as whole minutes."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0 or seconds > _MINUTES_PER_DAY * 60:
        return None
    return seconds // 60


def _time_from_nepali_text(text: str | None) -> time | None:
    """A "०६:०७" wall clock from the site, as a `time`."""
    if not text:
        return None
    match = _CLOCK_RE.search(_to_ascii_digits(text))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _bs_label(detail: dict[str, Any]) -> str:
    """
    The Bikram Sambat date as a label: "कार्तिक ५, २०८३".

    The same shape the site's own rashifal page prints, so the header
    and the source say the same thing in the same order. The month
    and the day come from the page in Nepali numerals, which is how
    a Nepali reader expects to see them.
    """
    month = str(detail.get("monthNp") or "").strip()
    day = str(detail.get("dayBsNp") or "").strip()
    year = str(detail.get("yearBsNp") or "").strip()

    if month and day and year:
        return f"{month} {day}, {year}"
    return year


# =============================================================
# THE MODELS
# =============================================================

@dataclass(frozen=True)
class NepaliDay:
    """A day, in both calendars."""

    ad_date: date
    bs_year: int
    bs_month: int
    bs_day: int
    bs_label: str
    weekday: str
    weekday_np: str

    @property
    def bs_key(self) -> str:
        """The Bikram Sambat date as the site's URLs spell it."""
        return f"{self.bs_year}-{self.bs_month}-{self.bs_day}"

    @property
    def path(self) -> str:
        """The path of this day's page on the site."""
        return DATE_PATH_TEMPLATE.format(
            year=self.bs_year, month=self.bs_month, day=self.bs_day
        )


@dataclass(frozen=True)
class TimeWindow:
    """One auspicious or inauspicious stretch of the day."""

    khanda: str
    period: str            # "day" or "night"
    result: str
    start_minute: int
    end_minute: int

    @property
    def is_auspicious(self) -> bool:
        return self.result in AUSPICIOUS_RESULTS

    def as_dict(self) -> dict[str, Any]:
        return {
            "khanda": self.khanda,
            "period": self.period,
            "from": _hhmm(self.start_minute),
            "to": _hhmm(self.end_minute),
            "result": self.result,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
        }


@dataclass(frozen=True)
class Panchang:
    """One day's almanac."""

    ad_date: date
    bs_year: int
    bs_month: int
    bs_day: int
    bs_label: str
    weekday: str
    weekday_np: str
    tithi: str
    tithi_full: str | None
    paksha: str | None
    nakshatra: str | None
    yoga: str | None
    karana: str | None
    moon_phase: str | None
    sunrise: time
    sunset: time
    day_length_minutes: int | None
    windows: tuple[TimeWindow, ...]
    source_url: str

    @property
    def bs_key(self) -> str:
        return f"{self.bs_year}-{self.bs_month}-{self.bs_day}"

    @property
    def auspicious(self) -> list[TimeWindow]:
        return [window for window in self.windows if window.is_auspicious]

    @property
    def inauspicious(self) -> list[TimeWindow]:
        return [window for window in self.windows if not window.is_auspicious]


# =============================================================
# PARSING
# =============================================================

def parse_today(html: str) -> NepaliDay:
    """
    The current Bikram Sambat date, from any page of the site.

    The "today" block is rendered into every page, so this works on
    the rashifal page and on the per-day page alike.
    """
    bootstrap = extract_bootstrap(html)
    today = bootstrap.get("today")

    if not isinstance(today, dict):
        raise PanchangParseError("the page carries no 'today' block")

    try:
        return NepaliDay(
            ad_date=date(
                int(today["year_ad"]),
                int(today["month_ad"]),
                int(today["date_ad"]),
            ),
            bs_year=int(today["year_bs"]),
            bs_month=int(today["month_bs"]),
            bs_day=int(today["date_bs"]),
            bs_label=str(today.get("bsFormatted") or ""),
            weekday=str(today.get("day_en") or ""),
            weekday_np=str(today.get("day_np") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PanchangParseError(
            f"the 'today' block is missing a field: {exc}"
        ) from exc


def parse_time_windows(
    html: str, *, sunrise: time, sunset: time
) -> list[TimeWindow]:
    """
    The फल tables: the auspicious and inauspicious windows of the day
    and of the night.

    These two tables are the site's own statement of which hours are
    good and which are not. They are rendered server-side and appear
    in no JSON block, so they are read out of the HTML — but only
    these two tables, and only their four cells.
    """
    sunrise_minute = sunrise.hour * 60 + sunrise.minute
    sunset_minute = sunset.hour * 60 + sunset.minute

    soup = BeautifulSoup(html or "", "lxml")
    windows: list[TimeWindow] = []

    for heading in soup.find_all("h3"):
        period = _PHALA_TABLES.get(heading.get_text(strip=True))
        if period is None:
            continue

        table = _table_after(heading)
        if table is None:
            continue

        windows.extend(
            _windows_from_table(table, period, sunrise_minute, sunset_minute)
        )

    return windows


def _table_after(heading) -> Any:
    """
    The table that belongs to a heading, if there is one.

    Scoped to the heading rather than to the whole document: the
    search stops at the next heading, so a page that later grows an
    unrelated table does not have that table read as a फल table.
    """
    for candidate in heading.find_all_next(["table", "h3"]):
        if candidate.name == "h3":
            # The next heading opens a different section, so this
            # heading has no table of its own.
            return None
        return candidate
    return None


def _windows_from_table(
    table, period: str, sunrise_minute: int, sunset_minute: int
) -> list[TimeWindow]:
    """One फल table, as a list of windows."""
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) == 4:
            rows.append(cells)

    if not rows:
        return []

    # The day runs sunrise to sunset; the night runs sunset to the
    # NEXT sunrise, so it crosses midnight. Those two bounds are what
    # make "रातको १२:१४" mean half past midnight rather than noon.
    if period == "day":
        floor, ceiling = sunrise_minute, sunset_minute
    else:
        floor, ceiling = sunset_minute, sunrise_minute + _MINUTES_PER_DAY

    starts = _unwrap_times(
        [_clock_minutes(row[1]) for row in rows], floor, ceiling
    )
    ends = _unwrap_times(
        [_clock_minutes(row[2]) for row in rows], floor, ceiling
    )

    windows: list[TimeWindow] = []
    for row, start, end in zip(rows, starts, ends):
        if start is None or end is None:
            continue

        result = row[3].strip().rstrip(":").strip()
        if result not in AUSPICIOUS_RESULTS:
            logger.debug(
                "panchang: unfamiliar फल %r; treating it as inauspicious.",
                result,
            )

        windows.append(
            TimeWindow(
                khanda=row[0].strip(),
                period=period,
                result=result,
                start_minute=start,
                end_minute=end,
            )
        )

    return windows


def parse_panchang(html: str, *, source_url: str = "") -> Panchang:
    """
    One day's almanac, from the site's per-day page.

    Raises `PanchangParseError` when the page is not what this parser
    expects. Callers treat that as "no panchang today" and move on;
    a half-parsed almanac is worse than none, because the header
    would show a tithi with no nakshatra beside it and nobody would
    know which half was wrong.
    """
    bootstrap = extract_bootstrap(html)

    data = bootstrap.get("data")
    detail = data.get("detail") if isinstance(data, dict) else None
    if not isinstance(detail, dict):
        raise PanchangParseError("the page carries no panchang detail")

    try:
        ad_date = date(
            int(detail["year_ad"]),
            int(detail["month_ad"]),
            int(detail["day_ad"]),
        )
        bs_year = int(detail["year_bs"])
        bs_month = int(detail["month_bs"])
        bs_day = int(detail["day_bs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PanchangParseError(
            f"the panchang detail is missing a date: {exc}"
        ) from exc

    panchanga = detail.get("panchanga")
    if not isinstance(panchanga, dict):
        raise PanchangParseError("the panchang detail has no panchanga block")

    sunrise_minute = _minutes_from_seconds(panchanga.get("sunrise"))
    sunset_minute = _minutes_from_seconds(panchanga.get("sunset"))
    sunrise = (
        time(hour=sunrise_minute // 60, minute=sunrise_minute % 60)
        if sunrise_minute is not None
        else _time_from_nepali_text(detail.get("sunriseNp"))
    )
    sunset = (
        time(hour=sunset_minute // 60, minute=sunset_minute % 60)
        if sunset_minute is not None
        else _time_from_nepali_text(detail.get("sunsetNp"))
    )

    if sunrise is None or sunset is None:
        raise PanchangParseError("the panchang has no sunrise or sunset")

    text = detail.get("panchangaText")
    text = text if isinstance(text, dict) else {}

    tithi = str(detail.get("tithiShort") or "").strip()
    if not tithi:
        raise PanchangParseError("the panchang has no tithi")

    nakshatra = str(text.get("nakshatra") or "").strip() or None
    if not nakshatra:
        raise PanchangParseError("the panchang has no nakshatra")

    paksha_index = panchanga.get("paksha")
    paksha = PAKSHA_NAMES.get(paksha_index) if isinstance(paksha_index, int) else None

    moon = detail.get("moonPhase")
    moon_name = ""
    if isinstance(moon, dict) and isinstance(moon.get("name"), dict):
        moon_name = str(moon["name"].get("np") or "").strip()

    day_length = None
    if sunset_minute is not None and sunrise_minute is not None:
        day_length = sunset_minute - sunrise_minute

    windows = parse_time_windows(
        html, sunrise=sunrise, sunset=sunset
    )

    return Panchang(
        ad_date=ad_date,
        bs_year=bs_year,
        bs_month=bs_month,
        bs_day=bs_day,
        bs_label=_bs_label(detail),
        weekday=str(detail.get("weekdayEn") or "").strip(),
        weekday_np=str(detail.get("weekdayNp") or "").strip(),
        tithi=tithi,
        tithi_full=str(text.get("tithi") or "").strip() or None,
        paksha=paksha,
        nakshatra=nakshatra,
        yoga=str(text.get("yoga") or "").strip() or None,
        karana=str(text.get("karana") or "").strip() or None,
        moon_phase=moon_name or None,
        sunrise=sunrise,
        sunset=sunset,
        day_length_minutes=day_length,
        windows=tuple(windows),
        source_url=source_url,
    )


# =============================================================
# FETCHING
# =============================================================

def _user_agent() -> str:
    from app.config import get_settings

    return get_settings().default_user_agent


def _timeout() -> float:
    from app.config import get_settings

    return float(get_settings().panchang_http_timeout_seconds)


def _build_client() -> httpx.AsyncClient:
    """
    An HTTP client for the source.

    `follow_redirects` is not optional: hamropatro.com answers a
    301 to www.hamropatro.com for every path, and a client that does
    not follow it sees a redirect body where it expected a page.
    """
    return httpx.AsyncClient(
        timeout=_timeout(),
        follow_redirects=True,
        headers={"User-Agent": _user_agent()},
    )


async def _get_html(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def fetch_today_html(client: httpx.AsyncClient) -> str:
    """
    The rashifal page, which is also where the site says what day it
    is.

    This is the page the pass fetches on every run: it carries the
    "today" block, so it is how the current Bikram Sambat date is
    resolved. It is also the page the twelve horoscopes live on, so
    the horoscopes cost no extra request - see app/services/rashifal.py.
    """
    return await _get_html(client, f"{PANCHANG_SOURCE}{RASHIFAL_PATH}")


async def fetch_today(client: httpx.AsyncClient | None = None) -> NepaliDay:
    """The current Bikram Sambat date. Raises `PanchangError`."""
    owned = client is None
    client = client or _build_client()
    try:
        html = await fetch_today_html(client)
        return parse_today(html)
    except httpx.HTTPError as exc:
        raise PanchangError(f"could not fetch {RASHIFAL_PATH}: {exc}") from exc
    finally:
        if owned:
            await client.aclose()


async def fetch_panchang(
    day: NepaliDay | None = None, client: httpx.AsyncClient | None = None
) -> Panchang:
    """
    One day's almanac. Raises `PanchangError`.

    `day` is the Bikram Sambat date to ask for; today when it is not
    given. Because `/date/` is a real archive, any past or future day
    can be fetched the same way, which is how a history is built.
    """
    owned = client is None
    client = client or _build_client()
    try:
        if day is None:
            day = await fetch_today(client)

        url = f"{PANCHANG_SOURCE}{day.path}"
        html = await _get_html(client, url)
        return parse_panchang(html, source_url=url)
    except httpx.HTTPError as exc:
        raise PanchangError(f"could not fetch {day.path}: {exc}") from exc
    finally:
        if owned:
            await client.aclose()


# =============================================================
# THE UPCOMING DAYS
# =============================================================

#: How many days AHEAD of today the almanac is kept.
#:
#: A Nepali reader asks two kinds of question about the panchang:
#: "what is today" and "what is tomorrow" — tomorrow's tithi when
#: picking a muhurat, tomorrow's sunrise when planning a trip, the
#: horoscope for the day after a festival. Today alone answers none
#: of the second kind.
#:
#: Seven is a week: it covers "the rest of this week" for a reader
#: planning something, and it is a bounded number of requests on a
#: source that is not ours.
UPCOMING_DAYS = 7


def next_day_href(html: str) -> str | None:
    """
    The path of the day AFTER this one, as the site spells it.

    Every per-day page carries a `nextHref` in its JSON island: the
    path of the following day. That link is the site's OWN conversion
    of one day into the next, so following it is the only way to walk
    forward across a month boundary without reimplementing the
    Bikram Sambat calendar here — and the BS calendar's month lengths
    are exactly the kind of thing that is wrong in a hand-rolled
    table.

    Returns None when the page carries no such link, which is the end
    of the walk.
    """
    try:
        bootstrap = extract_bootstrap(html)
    except PanchangParseError:
        return None

    data = bootstrap.get("data")
    href = data.get("nextHref") if isinstance(data, dict) else None

    if not isinstance(href, str):
        return None

    href = href.strip()
    if not href.startswith("/date/"):
        return None

    return href


def _day_number(href: str) -> int | None:
    """The day number in a `/date/{year}-{month}-{day}` path."""
    match = re.match(r"^/date/(\d+)-(\d+)-(\d+)/?$", href or "")
    return int(match.group(3)) if match else None


# =============================================================
# WHAT IS ALREADY STORED
# =============================================================

def nepal_today(now: datetime | None = None) -> date:
    """
    Today, in Nepal.

    The almanac is a Nepali publication and its days are Nepali days.
    The worker runs in UTC, so its own calendar day is wrong for five
    hours and forty-five minutes of every day — and the almanac row
    keyed on the wrong day is a row nobody will ever read.
    """
    moment = now or datetime.now(timezone.utc)
    return (moment + NEPAL_UTC_OFFSET).date()


async def stored_panchang_days(
    db, since: date
) -> list[tuple[date, datetime | None]]:
    """
    The days already stored, from `since` onwards, and when each was
    last written.

    Used to decide whether the week ahead still needs fetching, and
    how recently the last attempt was made — so the walk is not
    repeated on every pass, and a walk that keeps failing is retried
    hourly rather than every five minutes.
    """
    try:
        response = await db_call(
            lambda: db.table("daily_panchang")
            .select("panchang_date, fetched_at")
            .gte("panchang_date", since.isoformat())
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a missing row is not a crash
        logger.warning("panchang: could not read the stored days: %s", exc)
        return []

    if response is None:
        return []

    rows = response.data or []
    found: list[tuple[date, datetime | None]] = []
    for row in rows:
        value = (row or {}).get("panchang_date")
        if not value:
            continue
        try:
            day = date.fromisoformat(str(value)[:10])
        except ValueError:
            continue

        stamp = (row or {}).get("fetched_at")
        fetched: datetime | None = None
        if stamp:
            try:
                fetched = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                fetched = None

        found.append((day, fetched))

    return found


async def panchang_fetched_at(db, day: date) -> datetime | None:
    """
    When the row for `day` was last written, if it is there.

    NOT `maybe_single()`
    -------------------
    The obvious way to ask for one row by its primary key is
    `maybe_single()`. In this client version it does not work: the
    `Accept: application/vnd.pgrst.object+json` header asks PostgREST
    for a bare object, PostgREST answers 406 when the result is empty,
    and the client's own `except` block then RETURNS None instead of
    a response. So `execute()` hands back `None` — and every caller
    that then reads `.data` dies on a `TypeError`.

    That is not a corner case: it is what happens on the first pass of
    every day, because the day's row is not there yet. The whole pass
    was aborting on it, which is why the almanac was never stored even
    after the migration was applied.

    So it is a list query with a limit of one, and the first row is
    taken if there is one. The same shape `stored_panchang_days` uses.
    """
    try:
        response = await db_call(
            lambda: db.table("daily_panchang")
            .select("fetched_at")
            .eq("panchang_date", day.isoformat())
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("panchang: could not read the fetched_at stamp: %s", exc)
        return None

    if response is None:
        return None

    rows = response.data or []
    if not rows:
        return None

    value = (rows[0] or {}).get("fetched_at")
    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


#: How old a stored almanac may be before the day is fetched again.
#:
#: The almanac for a day does not change — the tithi, the nakshatra
#: and the sun's times are settled before the day begins. But the
#: source does occasionally correct a day, and a correction that is
#: never picked up is a correction that never reaches the reader.
#:
#: An hour is frequent enough to catch a correction and rare enough
#: that the source sees one request an hour for the day it is on.
PANCHANG_MAX_AGE_SECONDS = 3600


#: How old the week ahead may be before it is walked again.
#:
#: THE WALK IS NOT ONLY FOR THE DAY ROLL-OVER
#: ------------------------------------------
#: The walk fetches the seven days after today, one per-day page each.
#: It used to run only when the horizon fell short — which is once a
#: day, at the rollover — and that is not often enough, because the
#: source publishes a day's horoscope DURING that day rather than the
#: night before. Measured on 2026-10-23: the source's per-day page for
#: tomorrow carried TODAY's horoscope word for word, and so did the
#: page for the day after; the horoscope for a day appears some hours
#: after the day begins.
#:
#: So the week ahead is walked every six hours: twenty-eight requests
#: a day to one publisher, which is nothing, and a horoscope that is
#: at most six hours behind the source's own.
PANCHANG_WALK_MAX_AGE_SECONDS = 6 * 3600



# =============================================================
# STORING
# =============================================================

def _instant(day: date, clock: time) -> str:
    """A wall-clock Nepal time as the instant it refers to."""
    moment = datetime.combine(
        day, clock, tzinfo=timezone(NEPAL_UTC_OFFSET)
    )
    return moment.isoformat()


def panchang_row(panchang: Panchang) -> dict[str, Any]:
    """
    The row for one day.

    Every key is written on every pass. This is an UPSERT, and a
    payload that omitted a column would leave yesterday's value
    standing in it — a sunrise that changed with the season and did
    not, a फल the source corrected and we did not. For a table with
    one row a day there is nothing to preserve by omission.
    """
    return {
        "panchang_date": panchang.ad_date.isoformat(),
        "bikram_sambat_year": panchang.bs_year,
        "bikram_sambat_month": panchang.bs_month,
        "bikram_sambat_day": panchang.bs_day,
        "bikram_sambat_label": panchang.bs_label,
        "weekday": panchang.weekday,
        "weekday_np": panchang.weekday_np,
        "tithi": panchang.tithi,
        "tithi_full": panchang.tithi_full,
        "paksha": panchang.paksha,
        "nakshatra": panchang.nakshatra,
        "yoga": panchang.yoga,
        "karana": panchang.karana,
        "moon_phase": panchang.moon_phase,
        "sunrise": panchang.sunrise.strftime("%H:%M"),
        "sunset": panchang.sunset.strftime("%H:%M"),
        "sunrise_at": _instant(panchang.ad_date, panchang.sunrise),
        "sunset_at": _instant(panchang.ad_date, panchang.sunset),
        "day_length_minutes": panchang.day_length_minutes,
        "auspicious_times": [
            window.as_dict() for window in panchang.auspicious
        ],
        "inauspicious_times": [
            window.as_dict() for window in panchang.inauspicious
        ],
        "source_url": panchang.source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def store_panchang(db, panchang: Panchang) -> bool:
    """
    Write one day's almanac. True when it was stored.

    Upserts on the Gregorian day, so re-running the pass is free: the
    row is refreshed in place and never duplicated. Never raises —
    a failed write is a missing header, not a reason to take the
    worker down.
    """
    row = panchang_row(panchang)

    try:
        await db_call(
            lambda: db.table("daily_panchang")
            .upsert(row, on_conflict=PANCHANG_CONFLICT)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a missing header is not a crash
        logger.warning(
            "panchang: could not store %s: %s. The table is created by "
            "supabase/migrations/%s.",
            panchang.ad_date, exc, PANCHANG_MIGRATION,
        )
        return False

    return True


# =============================================================
# THE PASS
# =============================================================

#: The Bikram Sambat date this process last stored.
#:
#: Module-level for the same reason the reels pipeline keeps its
#: timestamps there: the scheduler is a long-lived object and this has
#: to outlive any single pass. It is reset only on restart, which is
#: the correct semantics — a restart re-fetches today's page once and
#: then goes quiet again.
_last_stored_bs: str | None = None

#: The Bikram Sambat date whose twelve horoscopes this process last
#: stored.
#:
#: A SEPARATE marker from `_last_stored_bs`, on purpose, and the
#: reason is a missing table. The two are written by the same pass
#: and normally move together, but they must not be ONE marker: if
#: `daily_rashifal` has not been created yet, every write of the
#: horoscopes fails, and a shared marker would then either be never
#: set — which would make the pass re-download the large per-day page
#: every hour for ever — or be set anyway, which would mean the
#: horoscopes were never retried. Two markers, two independent
#: failures, one of which cannot affect the other.
_last_stored_signs_bs: str | None = None


def last_stored_bs() -> str | None:
    """The BS date this process last stored, for tests and logs."""
    return _last_stored_bs


def last_stored_signs_bs() -> str | None:
    """The BS date whose horoscopes this process last stored."""
    return _last_stored_signs_bs


def forget_last_stored_bs() -> None:
    """Forget the last stored BS date, so the next pass refetches."""
    global _last_stored_bs, _last_stored_signs_bs
    _last_stored_bs = None
    _last_stored_signs_bs = None


async def sync_panchang(
    db,
    *,
    client: httpx.AsyncClient | None = None,
    force: bool = False,
    ahead_days: int = UPCOMING_DAYS,
) -> Panchang | None:
    """
    One panchang pass: fetch today's almanac and store it.

    Returns the stored `Panchang`, or None when nothing was stored.
    Never raises — every failure is logged and turned into "no
    panchang this pass", because a day without an almanac is a
    missing line in a header and not a reason to stop the worker.

    THE PER-DAY PAGE IS ONLY FETCHED WHEN IT IS WORTH FETCHING
    ------------------------------------------------------------
    The almanac for a day does not change once the day has begun, so
    the pass first asks the DATABASE whether today's row is there
    and fresh. If it is, and the week ahead is whole, the pass
    costs one query and no requests at all.

    That matters because this pass runs from two places: the
    long-lived worker's hourly scheduler, and the five-minute cron
    in CI. Without the check, the cron would re-download the same
    large page every five minutes for ever, which is a hundred and
    forty-four downloads a day of a page that says the same thing.

    THE WEEK AHEAD
    --------------
    A reader asking "what is tomorrow's tithi" is asking a real
    question, and today alone cannot answer it. So the pass also
    walks the week ahead: each per-day page carries a link to the
    day after it, and following that link is the site's OWN
    conversion of one day into the next — the Bikram Sambat month
    lengths are exactly the sort of thing that goes wrong in a
    hand-rolled table, and this way the site does the arithmetic.

    The walk is skipped when the week ahead is already whole, so it
    runs once a day — when the day rolls over — and not on every
    pass.

    THE TWELVE HOROSCOPES GO OUT WITH IT
    ------------------------------------
    The rashifal page is the page this pass already fetches to learn
    what day it is, and the twelve signs are in that same response.
    So today's horoscopes cost NO extra request: they are parsed out
    of a page that was being fetched anyway.

    The days ahead carry their horoscopes too, on their own per-day
    pages, so the week ahead comes with a week of horoscope.

    They are stored separately, in `daily_rashifal` — twelve rows for
    the day rather than one — and their write is guarded separately
    too, so a failure in one cannot stop the other.
    """
    global _last_stored_bs, _last_stored_signs_bs

    # Imported here rather than at module scope: app.services.rashifal
    # imports extract_bootstrap from this module, and a module-level
    # import in the other direction would be a cycle.
    from app.services.rashifal import (
        RashifalParseError,
        delete_rashifal_day,
        parse_rashifal,
        parse_rashifal_day,
        same_horoscope,
        store_rashifal,
    )

    owned = client is None
    client = client or _build_client()

    try:
        # ---- Is there anything to do? ----
        #
        # TWO GUARDS, INDEPENDENT OF EACH OTHER
        # --------------------------------------
        # Today's almanac and the week ahead are separate jobs, and
        # they are guarded separately:
        #
        #   1. Today's almanac is refreshed when the stored row is
        #      missing or older than an hour. The almanac for a day
        #      does not change, so an hour-old row is still right; it
        #      is only fetched again so that a correction the source
        #      publishes is picked up.
        #
        #   2. The week ahead is walked when the horizon is short AND
        #      the last walk was at least an hour ago. The "at least
        #      an hour ago" is what stops a source that is down from
        #      being asked eight times every five minutes for ever.
        #
        # The local Nepal day is the key today's row is stored under,
        # so it is what the checks are made against. If the server's
        # clock were wrong the worst case is a redundant fetch, which
        # is harmless.
        local_today = nepal_today()
        now = datetime.now(timezone.utc)

        stored = await stored_panchang_days(db, local_today)
        newest = None
        last_walk = None
        for day, stamp in stored:
            if stamp is None:
                continue
            if newest is None or stamp > newest:
                newest = stamp
            # Today's row is refreshed on its own schedule, so it says
            # nothing about the walk. The days AHEAD are written by the
            # walk and by nothing else, so their newest stamp IS the
            # time of the last walk.
            if day != local_today and (last_walk is None or stamp > last_walk):
                last_walk = stamp

        horizon_short = len(stored) < ahead_days + 1
        # The walk is due when the week ahead has not been walked for
        # six hours, or when a day is missing from it and an hour has
        # passed since anything was fetched. The second clause brings
        # the new day in at the rollover; the first keeps the days
        # ahead fresh, because the source publishes a day's horoscope
        # during that day rather than the night before.
        gap_walk = (
            None if last_walk is None else (now - last_walk).total_seconds()
        )
        gap_any = None if newest is None else (now - newest).total_seconds()
        walk_due = (
            (gap_walk is not None and gap_walk >= PANCHANG_WALK_MAX_AGE_SECONDS)
            or (
                horizon_short
                and (gap_any is None or gap_any >= PANCHANG_MAX_AGE_SECONDS)
            )
        )

        today_fetched_at = await panchang_fetched_at(db, local_today)
        today_fresh = today_fetched_at is not None and (
            (now - today_fetched_at).total_seconds() < PANCHANG_MAX_AGE_SECONDS
        )

        if not force and today_fresh and not walk_due:
            logger.debug(
                "panchang: %s is fresh and the week ahead is whole; "
                "nothing to do.",
                local_today,
            )
            return None

        # ---- Today ----
        #
        # The rashifal page says what day it is, and carries today's
        # twelve horoscopes. It is the small page, and it is only
        # fetched when today's almanac is due.
        today: NepaliDay | None = None
        today_page: str | None = None
        panchang: Panchang | None = None
        # Today's twelve horoscopes. They are the answer the walk
        # compares each day ahead against: a day the source has not
        # published yet carries today's horoscope word for word, and
        # storing that would put today's horoscope under six other
        # dates. See rashifal.same_horoscope.
        today_signs: list = []

        if force or not today_fresh:
            try:
                today_html = await fetch_today_html(client)
            except httpx.HTTPError as exc:
                logger.warning(
                    "panchang: could not fetch %s: %s", RASHIFAL_PATH, exc
                )
                return None

            try:
                today = parse_today(today_html)
            except PanchangParseError as exc:
                logger.warning("panchang: could not resolve today's date: %s", exc)
                return None

            # The twelve horoscopes, out of the response we already
            # have. A page that has lost its signs is not a reason to
            # refuse the almanac, which is the other half of this pass.
            signs = []
            try:
                signs = parse_rashifal(today_html)
            except RashifalParseError as exc:
                logger.warning("rashifal: could not read the horoscopes: %s", exc)

            today_signs = signs

            if signs and (force or today.bs_key != _last_stored_signs_bs):
                if await store_rashifal(
                    db, signs, today.ad_date, f"{PANCHANG_SOURCE}{RASHIFAL_PATH}"
                ):
                    _last_stored_signs_bs = today.bs_key

            # Today's almanac. The per-day page is also the first step
            # of the walk, so it is kept.
            try:
                today_page = await _get_html(
                    client, f"{PANCHANG_SOURCE}{today.path}"
                )
                panchang = parse_panchang(
                    today_page, source_url=f"{PANCHANG_SOURCE}{today.path}"
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "panchang: could not fetch %s: %s", today.bs_key, exc
                )
                return None
            except PanchangParseError as exc:
                logger.warning("panchang: could not read %s: %s", today.bs_key, exc)
                return None

            if not await store_panchang(db, panchang):
                return None

            _last_stored_bs = panchang.bs_key

        # ---- The week ahead ----
        #
        # The walk needs today's page to start from, because today's
        # page is what links to tomorrow's. If today's almanac was
        # fresh and the page was not fetched, the walk fetches it.
        if walk_due:
            if today_page is None:
                if today is None:
                    # The day was never resolved, so the walk cannot
                    # start. Resolve it and try again below.
                    try:
                        today_html = await fetch_today_html(client)
                        today = parse_today(today_html)
                    except (httpx.HTTPError, PanchangParseError) as exc:
                        logger.warning(
                            "panchang: could not resolve today for the week "
                            "ahead: %s",
                            exc,
                        )
                        return panchang

                try:
                    today_page = await _get_html(
                        client, f"{PANCHANG_SOURCE}{today.path}"
                    )
                except httpx.HTTPError as exc:
                    logger.warning(
                        "panchang: could not fetch %s for the week ahead: %s",
                        today.bs_key,
                        exc,
                    )
                    return panchang

            await _walk_the_days_ahead(
                db,
                client,
                first_page=today_page,
                ahead_days=ahead_days,
                parse_signs=parse_rashifal_day,
                store_signs=store_rashifal,
                today_signs=today_signs,
                delete_signs=delete_rashifal_day,
                is_published=same_horoscope,
            )

        return panchang
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the scheduler must not see this
        logger.exception("panchang: pass aborted; the scheduler keeps running.")
        return None
    finally:
        if owned:
            await client.aclose()


async def _walk_the_days_ahead(
    db,
    client: httpx.AsyncClient,
    *,
    first_page: str,
    ahead_days: int,
    parse_signs,
    store_signs,
    today_signs: list | None = None,
    delete_signs=None,
    is_published=None,
) -> list[date]:
    """
    Fetch and store the days after today.

    Each per-day page carries a link to the day after it, and the
    link is followed until the week ahead is whole. Every day that
    is fetched is stored: the almanac in `daily_panchang` and the
    twelve horoscopes in `daily_rashifal`, because the per-day page
    carries both.

    A DAY WHOSE HOROSCOPE THE SOURCE HAS NOT PUBLISHED IS NOT STORED
    ---------------------------------------------------------------
    The source serves TODAY's horoscope on the per-day page of a day
    it has not published yet - measured on 2026-10-23, tomorrow's page
    and the day after's both carried today's twelve horoscopes word
    for word. Storing that copy would put today's horoscope under
    six other dates: the same twelve sentences on seven different
    days, each with a different day of the week beside it, which is
    what the site was showing and what the reader called wrong
    information.

    So a day whose horoscope is identical to today's has not been
    published, and its horoscopes are removed rather than stored. The
    ALMANAC for that day is a different matter - the tithi, the
    sunrise and the sunset are computed for the day and are correct -
    so it is stored as usual. The next pass stores the horoscope the
    moment the source publishes it.

    Returns the days it stored. Never raises: a day that cannot be
    fetched is a gap in the week ahead, not a reason to stop the
    worker.
    """
    stored: list[date] = []

    # Start at TOMORROW. Today's own almanac was already fetched and
    # stored by the caller, and storing it a second time would be a
    # wasted write and a second row in the log for the same day.
    href = next_day_href(first_page)
    if not href:
        logger.debug("panchang: today has no next day; the week ahead is empty.")
        return stored

    for _ in range(ahead_days):
        try:
            html = await _get_html(client, f"{PANCHANG_SOURCE}{href}")
        except httpx.HTTPError as exc:
            logger.warning(
                "panchang: could not fetch %s for the week ahead: %s",
                href,
                exc,
            )
            break

        try:
            panchang = parse_panchang(
                html, source_url=f"{PANCHANG_SOURCE}{href}"
            )
        except PanchangParseError as exc:
            logger.warning("panchang: could not read %s: %s", href, exc)
            break

        if await store_panchang(db, panchang):
            stored.append(panchang.ad_date)

        # The horoscopes for that day, off the same page.
        try:
            signs = parse_signs(html)
        except Exception as exc:  # noqa: BLE001 - one day without signs
            logger.debug("rashifal: no horoscopes on %s: %s", href, exc)
            signs = []

        # Has the source published THIS day's horoscope, or is it still
        # repeating today's?
        unpublished = bool(
            signs
            and today_signs
            and is_published is not None
            and is_published(today_signs, signs)
        )

        if unpublished:
            logger.info(
                "rashifal: %s still carries today's horoscope, so the source "
                "has not published it yet; the copy we held is removed.",
                panchang.ad_date.isoformat(),
            )
            if delete_signs is not None:
                await delete_signs(db, panchang.ad_date)
        elif signs:
            await store_signs(db, signs, panchang.ad_date, f"{PANCHANG_SOURCE}{href}")

        following = next_day_href(html)
        if not following:
            logger.debug("panchang: %s has no next day; the week ahead ends.", href)
            break

        href = following

    if stored:
        logger.info(
            "panchang: stored %d day(s) of the week ahead (%s .. %s).",
            len(stored),
            stored[0].isoformat(),
            stored[-1].isoformat(),
        )

    return stored

