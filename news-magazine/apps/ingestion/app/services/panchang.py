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
   from. It is a small page and it is fetched on every pass.

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


async def fetch_today(client: httpx.AsyncClient | None = None) -> NepaliDay:
    """The current Bikram Sambat date. Raises `PanchangError`."""
    owned = client is None
    client = client or _build_client()
    try:
        html = await _get_html(client, f"{PANCHANG_SOURCE}{RASHIFAL_PATH}")
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


def last_stored_bs() -> str | None:
    """The BS date this process last stored, for tests and logs."""
    return _last_stored_bs


def forget_last_stored_bs() -> None:
    """Forget the last stored BS date, so the next pass refetches."""
    global _last_stored_bs
    _last_stored_bs = None


async def sync_panchang(
    db,
    *,
    client: httpx.AsyncClient | None = None,
    force: bool = False,
) -> Panchang | None:
    """
    One panchang pass: fetch today's almanac and store it.

    Returns the stored `Panchang`, or None when nothing was stored.
    Never raises — every failure is logged and turned into "no
    panchang this pass", because a day without an almanac is a
    missing line in a header and not a reason to stop the worker.

    The per-day page is only fetched when the day has rolled over.
    The job can then run as often as the operator likes without
    re-downloading a large page every time.
    """
    global _last_stored_bs

    owned = client is None
    client = client or _build_client()

    try:
        try:
            today = await fetch_today(client)
        except PanchangError as exc:
            logger.warning("panchang: could not resolve today's date: %s", exc)
            return None

        if not force and today.bs_key == _last_stored_bs:
            logger.debug(
                "panchang: %s is already stored; nothing to do.", today.bs_key
            )
            return None

        try:
            panchang = await fetch_panchang(today, client=client)
        except PanchangError as exc:
            logger.warning("panchang: could not fetch %s: %s", today.bs_key, exc)
            return None

        if not await store_panchang(db, panchang):
            return None

        _last_stored_bs = panchang.bs_key
        return panchang
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the scheduler must not see this
        logger.exception("panchang: pass aborted; the scheduler keeps running.")
        return None
    finally:
        if owned:
            await client.aclose()
