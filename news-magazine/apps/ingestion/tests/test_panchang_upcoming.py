"""
The week ahead.

WHAT THIS IS
------------
A reader who asks for "tomorrow's tithi" is asking a real question,
and only today has a row in the table unless the worker keeps the
days ahead. These tests are about that walk: the per-day pages
carry a link to the next day, the link is followed, and each day
that is fetched is stored with its own twelve horoscopes.

WHY THE LINK AND NOT A CONVERSION TABLE
---------------------------------------
The Bikram Sambat month is 29 to 32 days long and the calendar has
occasional repeats, so "the seventh day from today" cannot be worked
out by adding seven to today's BS date. The source's own `nextHref`
is the site's conversion of one day into the next, and following it
means the site does that arithmetic and we do not have to.

WHAT CAN GO WRONG HERE
----------------------
The walk must not loop for ever, must not store today twice, must
not re-fetch a week that is already stored, and must not fall over
when a page in the middle of the week is missing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from app.services.panchang import (
    UPCOMING_DAYS,
    nepal_today,
    next_day_href,
    parse_panchang,
    sync_panchang,
)
from app.services.rashifal import parse_rashifal_day


def run(coro):
    return asyncio.run(coro)


# =============================================================
# THE FIXTURES
# =============================================================

def _signs(count: int = 12) -> list[dict]:
    """Twelve signs, in the shape the per-day page serves them."""
    slugs = (
        "mesh", "brish", "mithun", "karkat", "singha", "kanya",
        "tula", "brischik", "dhanu", "makar", "kumbha", "meen",
    )
    return [
        {
            "slug": slug,
            "np": f"राशि {index + 1}",
            "en": f"Sign {index + 1}",
            "emoji": "♈",
            "initials": "चु, चे",
            "descNp": f"आजको शुभ रंग गुलाबी हो भने शुभ अंक {index + 1} रहेको छ।",
            "descEn": "",
        }
        for index, slug in enumerate(slugs[:count])
    ]


def _day_page(
    *,
    ad: tuple[int, int, int],
    bs: tuple[int, int, int],
    next_href: str | None = None,
    with_signs: bool = True,
) -> str:
    """A per-day page, in the shape the site serves one."""
    year_ad, month_ad, day_ad = ad
    year_bs, month_bs, day_bs = bs

    detail = {
        "year_ad": year_ad,
        "month_ad": month_ad,
        "day_ad": day_ad,
        "year_bs": year_bs,
        "month_bs": month_bs,
        "day_bs": day_bs,
        "weekdayEn": "Thursday",
        "weekdayNp": "बिहिवार",
        "tithiShort": "एकादशी",
        "panchanga": {"sunrise": 22200, "sunset": 62700, "paksha": 0},
        "panchangaText": {
            "tithi": "असोज शुक्ल एकादशी",
            "nakshatra": "पूर्वभाद्र",
            "yoga": "सिद्धि",
            "karana": "बव",
        },
        "moonPhase": {"name": {"np": "बाल चन्द्र"}},
        "dayBsNp": "५",
        "monthNp": "कार्तिक",
        "yearBsNp": "२०८३",
    }

    data: dict = {"detail": detail}
    if next_href:
        data["nextHref"] = next_href
    if with_signs:
        data["signs"] = _signs()

    payload = {
        "path": f"/date/{year_bs}-{month_bs}-{day_bs}",
        "today": {
            "year_ad": 2026, "month_ad": 10, "date_ad": 22,
            "year_bs": 2083, "month_bs": 7, "date_bs": 5,
            "day_en": "Thursday", "day_np": "बिहिवार",
            "bsFormatted": "कार्तिक ५, २०८३",
        },
        "data": data,
    }

    return (
        "<!DOCTYPE html><html lang='ne'><body>"
        "<script id=\"hp-bootstrap\" type=\"application/json\">"
        f"{json.dumps(payload, ensure_ascii=False)}"
        "</script></body></html>"
    )


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingClient:
    """An httpx.AsyncClient stand-in that remembers what it was asked."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise RuntimeError(f"no fixture for {url}")
        return FakeResponse(self.responses[url])

    async def aclose(self):
        pass


class FakeData:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, recorder, table):
        self._recorder = recorder
        self._table = table
        self._filters: list[tuple[str, str, str]] = []
        self._mode = "write"
        self._limit: int | None = None

    def upsert(self, row, on_conflict=None):
        self._recorder["rows"].append((self._table, row, on_conflict))
        self._recorder.setdefault("stored", {}).setdefault(
            self._table, []
        ).extend(row if isinstance(row, list) else [row])
        return self

    def select(self, columns):
        self._mode = "read"
        return self

    def gte(self, column, value):
        self._filters.append(("gte", column, str(value)))
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, str(value)))
        return self

    def maybe_single(self):
        self._mode = "single"
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        if self._mode == "write":
            return self._recorder

        rows = list(self._recorder.get("stored", {}).get(self._table, []))
        for kind, column, value in self._filters:
            if kind == "gte":
                rows = [
                    row for row in rows
                    if str(row.get(column, "")) >= value
                ]
            else:
                rows = [
                    row for row in rows
                    if str(row.get(column, "")) == value
                ]

        if self._mode == "single":
            return FakeData(rows[0] if rows else None)

        limit = getattr(self, "_limit", None)
        if limit is not None:
            rows = rows[:limit]
        return FakeData(rows)


class FakeDB:
    def __init__(self):
        self.rows: list[tuple] = []
        self.stored: dict[str, list[dict]] = {}
        self.recorder = {"rows": self.rows, "stored": self.stored}

    def table(self, name):
        return FakeQuery(self.recorder, name)

    def rows_for(self, table: str) -> list[dict]:
        return self.stored.get(table, [])


# =============================================================
# THE LINK
# =============================================================

def test_the_next_day_is_read_off_the_page():
    """
    Every per-day page links to the day after it. That link is the
    site's own conversion of one day into the next.
    """
    html = _day_page(ad=(2026, 10, 23), bs=(2083, 7, 6), next_href="/date/2083-7-7")

    assert next_day_href(html) == "/date/2083-7-7"


def test_a_page_with_no_next_day_ends_the_walk():
    """
    The last day the site knows about has no next day, and the walk
    must stop there rather than loop.
    """
    html = _day_page(ad=(2026, 10, 23), bs=(2083, 7, 6))

    assert next_day_href(html) is None


def test_a_link_to_something_that_is_not_a_day_is_ignored():
    """
    The site is free to put other links in that block. Only a path
    that is really a day is followed.
    """
    html = _day_page(
        ad=(2026, 10, 23), bs=(2083, 7, 6), next_href="/news/some-story"
    )

    assert next_day_href(html) is None


def test_a_page_that_is_not_the_sites_own_has_no_next_day():
    """
    A page with no JSON island is not a crash; it is the end of the
    walk.
    """
    assert next_day_href("<html><body>nothing here</body></html>") is None


# =============================================================
# THE WALK
# =============================================================

def _week_of_pages() -> dict[str, str]:
    """Today's page and the seven days after it."""
    pages = {
        "https://hamropatro.com/rashifal": _day_page(
            ad=(2026, 10, 22), bs=(2083, 7, 5), next_href="/date/2083-7-6"
        ),
        "https://hamropatro.com/date/2083-7-5": _day_page(
            ad=(2026, 10, 22), bs=(2083, 7, 5), next_href="/date/2083-7-6"
        ),
    }

    # The days after today: 23rd to 29th of October.
    for offset in range(1, 9):
        day = 5 + offset
        pages[f"https://hamropatro.com/date/2083-7-{day}"] = _day_page(
            ad=(2026, 10, 22 + offset),
            bs=(2083, 7, day),
            next_href=f"/date/2083-7-{day + 1}",
        )

    return pages


def test_the_walk_stores_the_days_after_today():
    """
    The days ahead are the whole point: a reader who asks for
    tomorrow's tithi gets an answer.
    """
    from app.services.panchang import forget_last_stored_bs

    forget_last_stored_bs()
    client = RecordingClient(_week_of_pages())
    db = FakeDB()

    run(sync_panchang(db, client=client))

    stored = {
        row["panchang_date"] for row in db.rows_for("daily_panchang")
    }

    assert "2026-10-22" in stored   # today
    assert "2026-10-23" in stored   # tomorrow
    assert "2026-10-29" in stored   # a week ahead


def test_the_walk_stops_at_the_horizon():
    """
    The week ahead is a week, not a month. A bounded number of
    requests on a source that is not ours.
    """
    from app.services.panchang import forget_last_stored_bs

    forget_last_stored_bs()
    client = RecordingClient(_week_of_pages())
    db = FakeDB()

    run(sync_panchang(db, client=client))

    # Today's page, plus one page for each of the seven days ahead.
    assert len(client.calls) == 2 + UPCOMING_DAYS


def test_the_walk_stores_the_horoscope_for_each_day():
    """
    The per-day page carries the twelve horoscopes as well as the
    almanac, so a day ahead has a horoscope and not only a tithi.
    """
    from app.services.panchang import forget_last_stored_bs

    forget_last_stored_bs()
    client = RecordingClient(_week_of_pages())
    db = FakeDB()

    run(sync_panchang(db, client=client))

    days = {row["panchang_date"] for row in db.rows_for("daily_rashifal")}

    assert "2026-10-23" in days
    assert "2026-10-29" in days

    # Twelve signs a day, each with a number from 1 to 12.
    tomorrow = [
        row for row in db.rows_for("daily_rashifal")
        if row["panchang_date"] == "2026-10-23"
    ]
    assert len(tomorrow) == 12
    assert sorted(row["sign"] for row in tomorrow) == list(range(1, 13))


def test_a_whole_week_is_not_walked_again():
    """
    The walk is for the days that are missing. A week that is already
    stored is not fetched a second time, or the five-minute cron would
    download eight pages every five minutes for ever.
    """
    from app.services.panchang import forget_last_stored_bs

    forget_last_stored_bs()
    client = RecordingClient(_week_of_pages())
    db = FakeDB()

    run(sync_panchang(db, client=client))
    first = len(client.calls)

    run(sync_panchang(db, client=client))

    assert len(client.calls) == first


def test_today_is_not_stored_twice():
    """
    The walk starts at TOMORROW. Today's own almanac was already
    fetched and stored by the pass, and storing it again would be a
    wasted write and a second row in the log for the same day.
    """
    from app.services.panchang import forget_last_stored_bs

    forget_last_stored_bs()
    client = RecordingClient(_week_of_pages())
    db = FakeDB()

    run(sync_panchang(db, client=client))

    today_rows = [
        row for row in db.rows_for("daily_panchang")
        if row["panchang_date"] == "2026-10-22"
    ]
    assert len(today_rows) == 1


# =============================================================
# THE PER-DAY HOROSCOPES
# =============================================================

def test_the_per_day_page_carries_the_twelve_signs():
    """
    The rashifal index page shows today. The per-day page shows that
    day — and the horoscopes for it live there, in a different place
    in the JSON.
    """
    html = _day_page(ad=(2026, 10, 23), bs=(2083, 7, 6))

    signs = parse_rashifal_day(html)

    assert len(signs) == 12
    assert signs[0].sign == 1
    assert signs[0].slug == "mesh"
    assert signs[0].name_ne == "राशि 1"
    assert signs[0].lucky_colour == "गुलाबी"
    assert signs[0].lucky_number == "1"


def test_the_per_day_page_of_a_day_with_no_signs_is_not_a_crash():
    """
    A day whose page has lost its horoscopes is still a day with an
    almanac. The almanac is the other half of the pass and must not
    be refused because the signs are missing.
    """
    from app.services.rashifal import RashifalParseError

    html = _day_page(ad=(2026, 10, 23), bs=(2083, 7, 6), with_signs=False)

    try:
        parse_rashifal_day(html)
    except RashifalParseError:
        return
    raise AssertionError("a page with no signs should raise")


# =============================================================
# NEPAL'S DAY
# =============================================================

def test_today_is_nepals_day_not_the_servers():
    """
    The worker runs in UTC. Its own calendar day is wrong for five
    hours and forty-five minutes of every day, and an almanac row
    keyed on the wrong day is a row nobody will ever read.
    """
    # 18:00 UTC is 23:45 in Nepal — still the same day.
    assert nepal_today(datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)) == date(2026, 10, 22)

    # 18:30 UTC is 00:15 the NEXT day in Nepal.
    assert nepal_today(datetime(2026, 10, 22, 18, 30, tzinfo=timezone.utc)) == date(2026, 10, 23)


def test_the_stored_row_is_read_back_with_its_timestamp():
    """
    The freshness check reads the row back. If the row cannot be
    read, the pass must treat the day as missing rather than crash —
    a missing almanac is a missing line in a header.
    """
    from app.services.panchang import panchang_fetched_at

    db = FakeDB()
    db.stored["daily_panchang"] = [
        {
            "panchang_date": "2026-10-22",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    ]

    stamp = run(panchang_fetched_at(db, date(2026, 10, 22)))

    assert stamp is not None
    assert (datetime.now(timezone.utc) - stamp) < timedelta(minutes=1)


def test_a_day_that_is_not_there_yet_is_not_an_error():
    """
    THE BUG THIS EXISTS FOR
    -----------------------
    The obvious way to ask the database for one row by its primary key
    is `maybe_single()`. In this client version it does not work: the
    `Accept: application/vnd.pgrst.object+json` header asks PostgREST
    for a bare object, PostgREST answers 406 when the result is empty,
    and the client's own `except` block RETURNS None instead of a
    response. So `execute()` hands back `None`, and every caller that
    then reads `.data` dies on a TypeError.

    It is not a corner case. It is what happens on the first pass of
    every day, because the day's row is not there yet — and the whole
    pass was aborting on it, which is why the almanac was never stored
    even after the migration was applied.

    So the read is a list query with a limit of one, and a day that is
    not there is None.
    """
    from app.services.panchang import panchang_fetched_at

    db = FakeDB()   # nothing stored at all

    stamp = run(panchang_fetched_at(db, date(2026, 10, 22)))

    assert stamp is None
