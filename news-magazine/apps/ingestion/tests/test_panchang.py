"""
The daily panchang: the Nepali almanac in the site header.

WHAT THESE TESTS PIN
--------------------
The parser is the only part of this feature that can be wrong
quietly. A page that parses into a plausible-looking row with the
tithi in the nakshatra column, or with the night's windows running
backwards, would show a confidently wrong almanac in the header of
every page on the site, and nothing would report an error.

So the fixtures below are SAVED RESPONSES, not invented ones: the
JSON blocks and the two फल tables are what hamropatro.com actually
served on 2026-10-22 (Kartik 5, 2083 BS), copied out of the live
page. The three cases are the three that can happen:

  1. a normal day  - everything present.
  2. a day with a missing optional field - the site omits the yoga,
     the karana, the moon phase and the फल tables, and the almanac
     still parses with those columns empty.
  3. a malformed response - not a page at all, and the parser says
     so instead of inventing a row.

THE TWO THINGS THAT ARE EASY TO GET WRONG
------------------------------------------
1. THE NIGHT CROSSES MIDNIGHT. "रातको ७:०८" is eight in the evening
   and "रातको १२:१४" is twenty past midnight, and the word in front
   of the number does not tell them apart. Only the sequence does.

2. THE NIGHT'S LAST WINDOW ENDS AT TOMORROW'S SUNRISE, which the
   site computes separately from today's. On the day this fixture was
   taken the night ended at 06:08 against a 06:07 sunrise - a minute
   apart, and a parser that assumed they were the same would drop the
   final window or turn it inside out.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.panchang import (
    AUSPICIOUS_RESULTS,
    PANCHANG_CONFLICT,
    PanchangParseError,
    forget_last_stored_bs,
    last_stored_bs,
    panchang_row,
    parse_panchang,
    parse_today,
    parse_time_windows,
    store_panchang,
    sync_panchang,
)


def run(coro):
    # asyncio.run(), not get_event_loop().run_until_complete().
    # get_event_loop() is deprecated and raises "no current event
    # loop" once another test has closed the loop it created, which
    # makes this helper order-dependent: these tests would pass alone
    # and fail in the full suite depending on which file ran first.
    return asyncio.run(coro)


# =============================================================
# THE SAVED FIXTURES
#
# The site's JSON island, copied out of the live page on 2026-10-22.
# The `today` block is on every page of the site; the `detail` block
# is on the per-day page.
# =============================================================

TODAY_BLOCK = {
    "year_ad": 2026,
    "month_ad": 10,
    "date_ad": 22,
    "year_bs": 2083,
    "month_bs": 7,
    "date_bs": 5,
    "day": 5,
    "month_ad_en": "October",
    "month_ad_np": "अक्टोबर",
    "month_bs_en": "Kartik",
    "month_bs_np": "कार्तिक",
    "date_bs_np": "५",
    "day_en": "Thursday",
    "day_np": "बिहिवार",
    "adKey": "2026-10-22",
    "bsFormatted": "कार्तिक ५, २०८३",
    "adFormatted": "22 October 2026",
    "weekdayNp": "बिहिवार",
    "weekdayEn": "Thursday",
}

DETAIL_BLOCK = {
    "year_ad": 2026,
    "month_ad": 10,
    "day_ad": 22,
    "year_bs": 2083,
    "month_bs": 7,
    "day_bs": 5,
    "dayOfWeek": 4,
    "inMonth": True,
    "dayBsNp": "५",
    "dayAdNp": "२२",
    "isToday": True,
    "isWeekendHoliday": False,
    "isHoliday": True,
    "daysRemaining": 0,
    "tithiShort": "एकादशी",
    "tithiShifted": False,
    "panchanga": {
        "nakshatraEndTime": 75729,
        "nakshtra": 23,
        "month": 5,
        "monthType": 0,
        "rashi": 10,
        "tithiEndTime": 54161,
        "tithi": 10,
        "paksha": 0,
        "yogaEndTime": 85891,
        "yoga": 10,
        "karanaEndTime": 54161,
        "karana": 6,
        "moonrise": 54151,
        "sunrise": 22061,
        "moonset": 7477,
        "sunset": 62913,
        "moonRashiEndTime": 9328,
    },
    "patroEvent": {"items": [], "isHoliday": True, "titleNp": "", "titleEn": ""},
    "userEvents": [],
    "weekdayNp": "बिहिवार",
    "weekdayEn": "Thursday",
    "monthNp": "कार्तिक",
    "monthEn": "Kartik",
    "yearBsNp": "२०८३",
    "panchangaText": {
        "tithi": "असोज शुक्ल एकादशी",
        "yoga": "वृद्धि",
        "karana": "विष्टि/भद्रा",
        "nakshatra": "शतभिषा",
    },
    "nepalSambat": {
        "year": "११४६",
        "month": "कौला",
        "paksha": "थ्व",
        "tithi": "एकादशी",
        "label": "नेपाल संवत् ११४६ कौलाथ्व एकादशी",
    },
    "moonPhase": {
        "emoji": "🌔",
        "name": {"np": "बढ्दो गिब्बस", "en": "Waxing Gibbous"},
    },
    "sunriseNp": "०६:०७",
    "sunsetNp": "१७:२८",
}

# The two फल tables, as the site rendered them. The फल labels carry a
# trailing colon in the markup and the parser has to strip it.
PHALA_TABLES_HTML = """
<div class="mt-4 grid grid-cols-1 gap-4">
  <div class="overflow-hidden rounded-card border border-border">
    <h3 class="border-b border-border bg-date-strip px-4 py-2 text-center font-np text-title-sm font-bold text-text-primary">दिन फल</h3>
    <table class="w-full text-left font-np text-body-sm">
      <thead><tr class="border-b border-border text-label-sm text-text-secondary">
        <th class="px-3 py-1.5 font-semibold">खण्ड</th>
        <th class="px-3 py-1.5 font-semibold">देखि</th>
        <th class="px-3 py-1.5 font-semibold">सम्म</th>
        <th class="px-3 py-1.5 font-semibold">फल</th>
      </tr></thead>
      <tbody>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">प्रथम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ६:०७</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ७:३२</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">शुभ:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">द्वितीय</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ७:३२</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ८:५७</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">रोग:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">तृतीय</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ८:५७</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको १०:२३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">उद्वेग:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">चतुर्थ</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको १०:२३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ११:४८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">चर:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">पञ्चम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ११:४८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">दिउँसोको १:१३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">लाभ:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">षष्ठम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">दिउँसोको १:१३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">दिउँसोको २:३८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">अमृत:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">सप्तम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">दिउँसोको २:३८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">साँझको ४:०३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">काल:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">अष्टम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">साँझको ४:०३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">साँझको ५:२८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">शुभ:</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="overflow-hidden rounded-card border border-border">
    <h3 class="border-b border-border bg-date-strip px-4 py-2 text-center font-np text-title-sm font-bold text-text-primary">रात्रि फल</h3>
    <table class="w-full text-left font-np text-body-sm">
      <thead><tr class="border-b border-border text-label-sm text-text-secondary">
        <th class="px-3 py-1.5 font-semibold">खण्ड</th>
        <th class="px-3 py-1.5 font-semibold">देखि</th>
        <th class="px-3 py-1.5 font-semibold">सम्म</th>
        <th class="px-3 py-1.5 font-semibold">फल</th>
      </tr></thead>
      <tbody>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">प्रथम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">साँझको ५:२८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ७:०३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">रोग:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">द्वितीय</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ७:०३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ८:३८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">उद्वेग:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">तृतीय</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ८:३८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको १०:१३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">चर:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">चतुर्थ</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको १०:१३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ११:४८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">लाभ:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">पञ्चम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको ११:४८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको १:२३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">अमृत:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">षष्ठम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको १:२३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको २:५८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">काल:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">सप्तम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">रातको २:५८</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ४:३३</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">शुभ:</td>
        </tr>
        <tr class="border-b border-border last:border-0">
          <td class="px-3 py-1.5 text-text-primary">अष्टम</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ४:३३</td>
          <td class="px-3 py-1.5 tabular-nums text-text-secondary">बिहानको ६:०८</td>
          <td class="px-3 py-1.5 font-semibold text-text-primary">रोग:</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
"""


def _page(
    today: dict | None = None,
    detail: dict | None = None,
    tables: str = "",
    *,
    raw_bootstrap: str | None = None,
) -> str:
    """Stitch a saved fixture back into the shape the site serves."""
    payload = {
        "path": "/date/2083-7-5",
        "locale": "np",
        "page": "date",
        "today": today if today is not None else TODAY_BLOCK,
        "data": {
            "detail": detail if detail is not None else DETAIL_BLOCK,
            "label": "२०८३ कार्तिक ५",
            "bs": "2083-7-5",
            "todayHref": "/date/2083-7-5",
        },
    }

    if raw_bootstrap is not None:
        body = raw_bootstrap
    else:
        body = json.dumps(payload, ensure_ascii=False)

    return (
        "<!DOCTYPE html><html lang='ne'><head><title>Hamro Patro</title></head>"
        "<body>"
        f"<script id=\"hp-bootstrap\" type=\"application/json\">{body}</script>"
        f"{tables}"
        "</body></html>"
    )


NORMAL_DAY_HTML = _page(tables=PHALA_TABLES_HTML)

#: The same day, with the optional parts left out: no yoga, no
#: karana, no moon phase and no फल tables. The site does serve days
#: like this - a day it has no panchang for says so on the page.
_missing_detail = json.loads(json.dumps(DETAIL_BLOCK))
_missing_detail["panchangaText"] = {
    "tithi": "असोज शुक्ल एकादशी",
    "nakshatra": "शतभिषा",
}
_missing_detail.pop("moonPhase", None)
MISSING_OPTIONAL_HTML = _page(detail=_missing_detail, tables="")

#: Not a page at all.
MALFORMED_HTML = (
    "<!DOCTYPE html><html><head><title>Hamro Patro</title></head>"
    "<body><h1>Something went wrong</h1></body></html>"
)

#: A page whose JSON island is not JSON.
BROKEN_JSON_HTML = _page(raw_bootstrap="{not json at all")


# =============================================================
# THE NORMAL DAY
# =============================================================


def test_parse_today_reads_the_bikram_sambat_date():
    """
    The BS date is what the source is addressed by, so it has to come
    out of the page exactly. Kartik is the seventh month, one-based -
    Baishakh is the first.
    """
    today = parse_today(NORMAL_DAY_HTML)

    assert today.bs_year == 2083
    assert today.bs_month == 7
    assert today.bs_day == 5
    assert today.bs_key == "2083-7-5"
    assert today.bs_label == "कार्तिक ५, २०८३"


def test_parse_today_reads_the_gregorian_date_and_the_weekday():
    """The header shows the date it is FOR, so it must be right."""
    today = parse_today(NORMAL_DAY_HTML)

    assert today.ad_date == date(2026, 10, 22)
    assert today.weekday == "Thursday"
    assert today.weekday_np == "बिहिवार"


def test_parse_today_gives_the_path_of_the_day_page():
    """
    The per-day page is addressed by BS date, and the month is
    1-based, so the path is built from the parts and not from a
    formatted string.
    """
    today = parse_today(NORMAL_DAY_HTML)

    assert today.path == "/date/2083-7-5"


def test_parse_panchang_reads_the_tithi_and_the_nakshatra():
    """The two the header is built around."""
    panchang = parse_panchang(NORMAL_DAY_HTML, source_url="https://x/date/2083-7-5")

    assert panchang.tithi == "एकादशी"
    assert panchang.tithi_full == "असोज शुक्ल एकादशी"
    assert panchang.nakshatra == "शतभिषा"
    assert panchang.paksha == "शुक्ल"
    assert panchang.yoga == "वृद्धि"
    assert panchang.karana == "विष्टि/भद्रा"


def test_parse_panchang_reads_sunrise_and_sunset():
    """
    Sunrise and sunset come from the panchanga block as seconds
    after midnight, and the site also prints them in Nepali numerals.
    Both have to land on the same clock time.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert panchang.sunrise == time(6, 7)
    assert panchang.sunset == time(17, 28)
    # 11h21m, which is what the page prints as "११ घण्टा २१ मिनेट".
    assert panchang.day_length_minutes == 681


def test_parse_panchang_reads_both_calendars_and_the_weekday():
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert panchang.ad_date == date(2026, 10, 22)
    assert panchang.bs_key == "2083-7-5"
    assert panchang.weekday == "Thursday"
    assert panchang.weekday_np == "बिहिवार"


def test_parse_panchang_reads_the_moon_phase():
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert panchang.moon_phase == "बढ्दो गिब्बस"


def test_the_bikram_sambat_label_is_the_way_the_source_prints_it():
    """
    The label is the date as a reader sees it - the month, the day and
    the year, in the order the site's own rashifal page prints them.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert panchang.bs_label == "कार्तिक ५, २०८३"


# =============================================================
# THE TIME WINDOWS
# =============================================================


def test_the_day_windows_start_at_sunrise_and_end_at_sunset():
    """
    The day's eight windows tile the daylight, and the first one
    starts at the sunrise the page also prints. If the windows and
    the sunrise disagree, one of them is wrong - and the windows are
    the ones a reader acts on.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    day = [w for w in panchang.windows if w.period == "day"]

    assert len(day) == 8
    assert day[0].start_minute == 6 * 60 + 7
    assert day[-1].end_minute == 17 * 60 + 28


def test_the_night_windows_run_past_midnight():
    """
    The night crosses midnight, and this is the case that breaks a
    naive parser.

    "रातको ११:४८" is 23:48 and "रातको १:२३" is 01:23 the NEXT
    morning. Read as plain clock values the second one would be
    BEFORE the first, and the night would run backwards.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    night = [w for w in panchang.windows if w.period == "night"]

    assert len(night) == 8
    assert night[0].start_minute == 17 * 60 + 28
    # 23:48 -> 01:23 the next morning.
    assert night[4].start_minute == 23 * 60 + 48
    assert night[4].end_minute == 1440 + 83
    # And the sequence only ever moves forward.
    starts = [w.start_minute for w in night]
    assert starts == sorted(starts)


def test_the_last_night_window_ends_at_tomorrows_sunrise():
    """
    The site's night ends at 06:08 and its sunrise for the day is
    06:07 - a minute apart, because they are computed for different
    days. A parser that treated them as the same number would either
    drop the final window or turn it inside out.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    night = [w for w in panchang.windows if w.period == "night"]

    assert night[-1].start_minute == 1440 + 4 * 60 + 33
    assert night[-1].end_minute == 1440 + 6 * 60 + 8
    assert night[-1].end_minute > night[-1].start_minute


def test_every_window_moves_forward():
    """
    A window that ends before it starts is a window that has been
    read inside out.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    for window in panchang.windows:
        assert window.end_minute > window.start_minute, window


def test_the_auspicious_and_inauspicious_windows_are_split_by_their_result():
    """
    शुभ, लाभ and अमृत are the panchang's good hours. रोग, उद्वेग,
    चर and काल are the four it counts against the day.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    auspicious = panchang.auspicious
    inauspicious = panchang.inauspicious

    assert len(auspicious) + len(inauspicious) == len(panchang.windows)
    assert {w.result for w in auspicious} <= AUSPICIOUS_RESULTS
    assert all(w.result not in AUSPICIOUS_RESULTS for w in inauspicious)

    # The fixture's day opens auspicious and closes inauspicious.
    assert auspicious[0].result == "शुभ"
    assert inauspicious[0].result == "रोग"


def test_the_window_labels_are_the_fresh_hours():
    """
    The label the site prints is kept, minus the colon it prints it
    with. The colon is a rendering artefact and a header that shows
    "रोग:" looks broken.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    for window in panchang.windows:
        assert not window.result.endswith(":"), window
        assert ":" not in window.result, window


def test_the_windows_carry_a_wall_clock_and_a_minute_count():
    """
    The wall clock is what the header prints and the minute count is
    what a reader's clock is compared against. The night runs past
    midnight, so the minute count can exceed a day.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    window = panchang.auspicious[0]

    assert window.as_dict()["from"] == "06:07"
    assert window.as_dict()["to"] == "07:32"
    assert window.as_dict()["start_minute"] == 367
    assert window.as_dict()["end_minute"] == 452
    assert window.as_dict()["period"] == "day"


def test_the_fala_result_of_an_unknown_fresh_hour_is_not_called_auspicious():
    """
    A फल this parser has never seen is not auspicious. Guessing
    otherwise would put a wrong hour in the header's good list.
    """
    html = _page(
        tables=PHALA_TABLES_HTML.replace("शुभ:", "रहस्य:")
    )

    panchang = parse_panchang(html)

    assert all(w.result != "रहस्य" for w in panchang.auspicious)
    assert any(w.result == "रहस्य" for w in panchang.inauspicious)


# =============================================================
# A DAY WITH A MISSING OPTIONAL FIELD
# =============================================================


def test_a_day_with_a_missing_optional_field_still_parses():
    """
    The site does serve days with parts missing. A missing yoga, a
    missing moon phase and missing फल tables must cost those
    columns and nothing else - the tithi, the nakshatra, the sunrise
    and the sunset are still there and still have to be right.
    """
    panchang = parse_panchang(MISSING_OPTIONAL_HTML)

    assert panchang.tithi == "एकादशी"
    assert panchang.nakshatra == "शतभिषा"
    assert panchang.sunrise == time(6, 7)
    assert panchang.sunset == time(17, 28)
    assert panchang.yoga is None
    assert panchang.karana is None
    assert panchang.moon_phase is None
    assert panchang.windows == ()


def test_a_day_with_no_windows_stores_empty_lists():
    """
    Empty, not null. A reader that iterates the list must not have
    to check for null first, and a missing table is not a reason to
    refuse the row.
    """
    panchang = parse_panchang(MISSING_OPTIONAL_HTML)
    row = panchang_row(panchang)

    assert row["auspicious_times"] == []
    assert row["inauspicious_times"] == []


def test_the_fresh_hour_tables_are_absent_not_empty_pages():
    """
    The two फल tables are found by their heading, and a page without
    them yields no windows rather than an error.
    """
    windows = parse_time_windows(
        MISSING_OPTIONAL_HTML, sunrise=time(6, 7), sunset=time(17, 28)
    )

    assert windows == []


# =============================================================
# A MALFORMED RESPONSE
# =============================================================


def test_a_malformed_response_is_rejected():
    """
    A page that is not the page we asked for must be an error, not a
    row with blank columns. The header would show a confidently
    wrong almanac and nothing would report it.
    """
    with pytest.raises(PanchangParseError):
        parse_panchang(MALFORMED_HTML)


def test_a_response_whose_json_is_broken_is_rejected():
    with pytest.raises(PanchangParseError):
        parse_panchang(BROKEN_JSON_HTML)


def test_a_response_with_no_today_block_is_rejected():
    with pytest.raises(PanchangParseError):
        parse_today(_page(today={}))


def test_a_response_with_no_panchang_detail_is_rejected():
    """
    The rashifal page has no per-day detail. Asking it for one must
    fail loudly rather than produce a row of empty strings.
    """
    with pytest.raises(PanchangParseError):
        parse_panchang(_page(detail={}))


def test_a_response_with_no_sunrise_is_rejected():
    """
    The फल tables cannot be placed on the clock without a sunrise
    and a sunset, so a day with neither is a day this parser cannot
    read, not a day with two null times.
    """
    detail = json.loads(json.dumps(DETAIL_BLOCK))
    del detail["panchanga"]["sunrise"]
    del detail["panchanga"]["sunset"]
    detail.pop("sunriseNp", None)
    detail.pop("sunsetNp", None)

    with pytest.raises(PanchangParseError):
        parse_panchang(_page(detail=detail))


def test_a_response_with_no_nakshatra_is_rejected():
    """The nakshatra is one of the two the header is built around."""
    detail = json.loads(json.dumps(DETAIL_BLOCK))
    detail["panchangaText"].pop("nakshatra", None)

    with pytest.raises(PanchangParseError):
        parse_panchang(_page(detail=detail))


# =============================================================
# THE ROW
# =============================================================


def test_the_row_is_keyed_by_the_gregorian_day():
    """
    The primary key is the Gregorian day, so re-running the day's
    fetch is an upsert and never a second row for the same day.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    row = panchang_row(panchang)

    assert row["panchang_date"] == "2026-10-22"
    assert PANCHANG_CONFLICT == "panchang_date"


def test_the_row_carries_both_calendars_and_the_weekday():
    panchang = parse_panchang(NORMAL_DAY_HTML)
    row = panchang_row(panchang)

    assert row["bikram_sambat_year"] == 2083
    assert row["bikram_sambat_month"] == 7
    assert row["bikram_sambat_day"] == 5
    assert row["bikram_sambat_label"] == "कार्तिक ५, २०८३"
    assert row["weekday"] == "Thursday"
    assert row["weekday_np"] == "बिहिवार"


def test_the_row_carries_the_times_as_a_clock_and_as_an_instant():
    """
    The wall clock is what the header prints; the instant is what a
    reader in another timezone needs. Nepal is UTC+05:45.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    row = panchang_row(panchang)

    assert row["sunrise"] == "06:07"
    assert row["sunset"] == "17:28"
    assert row["sunrise_at"] == "2026-10-22T06:07:00+05:45"
    assert row["sunset_at"] == "2026-10-22T17:28:00+05:45"


def test_the_row_carries_the_windows_split_into_two_lists():
    panchang = parse_panchang(NORMAL_DAY_HTML)
    row = panchang_row(panchang)

    assert len(row["auspicious_times"]) == 7
    assert len(row["inauspicious_times"]) == 9
    assert len(row["auspicious_times"]) + len(row["inauspicious_times"]) == 16
    assert row["auspicious_times"][0]["from"] == "06:07"


def test_the_row_always_writes_every_column():
    """
    Every key is written on every pass. An upsert that omitted a
    column would leave yesterday's value standing in it - a sunrise
    that changed with the season and did not.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)
    row = panchang_row(panchang)

    for column in (
        "panchang_date", "bikram_sambat_year", "bikram_sambat_month",
        "bikram_sambat_day", "bikram_sambat_label", "weekday", "weekday_np",
        "tithi", "tithi_full", "paksha", "nakshatra", "yoga", "karana",
        "moon_phase", "sunrise", "sunset", "sunrise_at", "sunset_at",
        "day_length_minutes", "auspicious_times", "inauspicious_times",
        "source_url", "fetched_at",
    ):
        assert column in row, column


# =============================================================
# STORING
# =============================================================


class FakeData:
    """A supabase-py response, with the rows the fake kept."""

    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, recorder, table):
        self._recorder = recorder
        self._table = table
        self._filters: list[tuple[str, str, str]] = []
        self._mode = "write"

    def upsert(self, row, on_conflict=None):
        self._recorder["rows"].append((self._table, row, on_conflict))
        # A real upsert leaves the row in the table, and the pass
        # reads the table back to decide whether it needs to fetch
        # anything. So the fake keeps it too.
        self._recorder.setdefault("stored", {}).setdefault(
            self._table, []
        ).append(row)
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
        return FakeData(rows)


class FakeDB:
    """A supabase-py stand-in that records what it was asked to write."""

    def __init__(self):
        self.rows: list[tuple] = []
        self.stored: dict[str, list[dict]] = {}
        self.recorder = {"rows": self.rows, "stored": self.stored}

    def table(self, name):
        return FakeQuery(self.recorder, name)


class ExplodingDB:
    def table(self, name):
        raise RuntimeError("the database is unreachable")


def test_the_row_is_written_as_an_upsert():
    """
    The upsert is on the Gregorian day, so a second pass of the same
    day refreshes the row instead of adding another one.
    """
    db = FakeDB()
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert run(store_panchang(db, panchang)) is True
    assert len(db.rows) == 1
    table, row, on_conflict = db.rows[0]
    assert table == "daily_panchang"
    assert on_conflict == "panchang_date"
    assert row["panchang_date"] == "2026-10-22"


def test_a_failed_write_is_reported_and_not_raised():
    """
    A missing header is not a reason to take the worker down.
    """
    panchang = parse_panchang(NORMAL_DAY_HTML)

    assert run(store_panchang(ExplodingDB(), panchang)) is False


# =============================================================
# THE PASS
# =============================================================


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


def test_the_pass_fetches_the_day_page_for_the_date_it_resolved():
    """
    The site is addressed by Bikram Sambat date, so the pass resolves
    today's date first and then asks for that day's page.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": NORMAL_DAY_HTML,
        }
    )
    db = FakeDB()

    panchang = run(sync_panchang(db, client=client))

    assert panchang is not None
    assert panchang.ad_date == date(2026, 10, 22)
    assert client.calls == [
        "https://hamropatro.com/rashifal",
        "https://hamropatro.com/date/2083-7-5",
    ]
    assert len(db.rows) == 1


def test_the_pass_does_not_refetch_a_day_it_has_already_stored():
    """
    The per-day page is a large one and the almanac does not change
    within a day, so the pass asks the DATABASE whether today's row
    is there and fresh — and when it is, it downloads nothing at all.

    This is what lets the five-minute cron run the pass. Without the
    check, the cron would re-download the same large page three
    hundred times a day, and the small page with it.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": NORMAL_DAY_HTML,
        }
    )
    db = FakeDB()

    run(sync_panchang(db, client=client))
    assert client.calls == [
        "https://hamropatro.com/rashifal",
        "https://hamropatro.com/date/2083-7-5",
    ]

    second = run(sync_panchang(db, client=client))

    assert second is None
    # Nothing is fetched at all: today's row is less than an hour old.
    assert client.calls == [
        "https://hamropatro.com/rashifal",
        "https://hamropatro.com/date/2083-7-5",
    ]
    assert len(db.rows) == 1


def test_the_pass_refetches_once_the_day_is_old_enough():
    """
    The almanac for a day does not change, but the source does
    occasionally correct one, and a correction that is never picked
    up is a correction that never reaches the reader. So an old row
    is refreshed.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": NORMAL_DAY_HTML,
        }
    )
    db = FakeDB()

    run(sync_panchang(db, client=client))

    # Age the stored row past the freshness window.
    stale = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    for row in db.stored.get("daily_panchang", []):
        row["fetched_at"] = stale

    run(sync_panchang(db, client=client))

    assert client.calls == [
        "https://hamropatro.com/rashifal",
        "https://hamropatro.com/date/2083-7-5",
        "https://hamropatro.com/rashifal",
        "https://hamropatro.com/date/2083-7-5",
    ]


def test_the_pass_does_not_raise_when_the_source_is_down():
    """
    A panchang that cannot be fetched is logged and skipped. The
    worker that is polling twenty-seven newsrooms must not hear
    about it.
    """
    forget_last_stored_bs()

    class BrokenClient:
        async def get(self, url):
            raise RuntimeError("the host is unreachable")

        async def aclose(self):
            pass

    db = FakeDB()

    assert run(sync_panchang(db, client=BrokenClient())) is None
    assert db.rows == []


def test_the_pass_does_not_raise_when_the_page_is_malformed():
    """
    The site changing its markup is a normal event. It must cost the
    header one line, not the scheduler a job.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": MALFORMED_HTML,
            "https://hamropatro.com/date/2083-7-5": MALFORMED_HTML,
        }
    )
    db = FakeDB()

    assert run(sync_panchang(db, client=client)) is None
    assert db.rows == []


def test_the_pass_never_stores_a_day_it_could_not_parse():
    """
    A half-parsed almanac is worse than none.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": MALFORMED_HTML,
        }
    )
    db = FakeDB()

    assert run(sync_panchang(db, client=client)) is None
    assert db.rows == []
    # And the day is not marked as stored, so the next pass tries
    # again rather than deciding the day is already done.
    assert last_stored_bs() is None


# =============================================================
# THE SCHEDULER JOB
# =============================================================


class FakeJob:
    def __init__(self, job_id):
        self.id = job_id
        self.removed = False

    def remove(self):
        self.removed = True


class FakeScheduler:
    def __init__(self, jobs=None):
        self.jobs = dict(jobs or {})
        self.added: list[dict] = []

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, fn, **kwargs):
        self.added.append({"fn": fn, **kwargs})
        self.jobs[kwargs["id"]] = FakeJob(kwargs["id"])


def _panchang_settings(**overrides):
    """The panchang fields of Settings, with everything else irrelevant."""
    defaults = dict(
        enable_panchang=True,
        panchang_poll_seconds=3600,
        panchang_http_timeout_seconds=20.0,
        default_user_agent="TestAgent/1.0",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_the_panchang_job_is_registered_only_when_the_almanac_is_enabled(monkeypatch):
    """
    The whole feature hangs off one setting, and the job must not
    exist on a deployment that has turned the almanac off.
    """
    from app.workers import scheduler as sched

    for enabled, expect_job in ((True, True), (False, False)):
        fake = FakeScheduler()
        monkeypatch.setattr(
            sched, "get_settings", lambda: _panchang_settings(enable_panchang=enabled)
        )
        monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

        sched._register_panchang_job()

        assert (sched.PANCHANG_JOB_ID in fake.jobs) is expect_job, enabled
        assert len(fake.added) is (1 if enabled else 0), enabled


def test_disabling_the_panchang_removes_a_job_that_is_already_running(monkeypatch):
    """
    Declining to ADD the job is not the same as turning it off. A
    process started with the almanac on would otherwise keep polling
    the source for the life of the container.
    """
    from app.workers import scheduler as sched

    existing = FakeJob(sched.PANCHANG_JOB_ID)
    fake = FakeScheduler({sched.PANCHANG_JOB_ID: existing})
    monkeypatch.setattr(
        sched, "get_settings", lambda: _panchang_settings(enable_panchang=False)
    )
    monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

    sched._register_panchang_job()

    assert existing.removed
    assert fake.added == []


def test_the_panchang_job_never_raises_into_the_scheduler(monkeypatch):
    """
    The job lives in a scheduler that also runs the outlets. An
    exception escaping here is an unhandled APScheduler job error,
    and one broken almanac must not be able to take the news
    pipeline down with it.
    """
    from app.workers import scheduler as sched
    from app.services import panchang as panchang_module

    def explode(*args, **kwargs):
        raise RuntimeError("the source is on fire")

    monkeypatch.setattr(panchang_module, "sync_panchang", explode)

    # Does not raise. That is the whole assertion.
    run(sched._sync_panchang())
