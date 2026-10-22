"""
The twelve-sign rashifal: today's horoscope for each of the twelve
zodiac signs.

WHAT THIS IS
------------
The site calls the page "राशिफल" and it is the page the operator
asked for: the twelve horoscopes — मेष (Aries) through मीन
(Pisces) — each with a few lines of advice, a lucky colour and a
lucky number.

It is a DIFFERENT THING from the panchang in `panchang.py`, and it
has its own table (`daily_rashifal`, migration
0025_daily_rashifal.sql) because its grain is different: the
panchang is one row per day, this is twelve rows per day.

WHY IT IS A SEPARATE MODULE AND NOT PART OF panchang.py
--------------------------------------------------------
Because the two are fetched from DIFFERENT pages and stored in
DIFFERENT tables, and mixing them would make both harder to read.

But they share a REQUEST, and that is the point of this module: the
`/rashifal` page is the page the panchang pass already fetches to
learn what day it is, and the twelve signs are sitting in that same
response. So the horoscopes cost NO extra request at all — they are
parsed out of a page that was being fetched anyway.

The pass that owns the request lives in `panchang.py`
(`sync_panchang`), and it calls into here. `panchang.py` imports
this module INSIDE its function, not at module scope: this module
imports `extract_bootstrap` from `panchang.py` at module scope, and
a module-level import in the other direction would be a cycle.

robots.txt was read before any of this was written:

    User-Agent: *
    Disallow: /widgets/
    Disallow: /login
    Disallow: /en/login
    Disallow: /contest-term-condition
    Disallow: /terms
    Disallow: /privacy
    Disallow: /bookmark
    Disallow: /notes

It is a blacklist and `/rashifal` is not on it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.services.ingestion import db_call
from app.services.panchang import PanchangParseError, extract_bootstrap

logger = logging.getLogger("rashifal")


# =============================================================
# THE SOURCE
# =============================================================

#: The page. The panchang pass fetches it to resolve today's date,
#: and the twelve signs come out of the same response.
RASHIFAL_SOURCE = "https://hamropatro.com"
RASHIFAL_PATH = "/rashifal"

#: The migration that creates `daily_rashifal`. Named in log messages
#: on purpose: "something is wrong" is useless at 3am, "apply
#: 0025_daily_rashifal.sql" is a thing the operator can do.
RASHIFAL_MIGRATION = "0025_daily_rashifal.sql"

#: The upsert key. The Gregorian day and the sign, together — the
#: primary key of the table.
RASHIFAL_CONFLICT = "panchang_date,sign"

#: How many signs there are. The zodiac has twelve and the site
#: publishes all twelve, in this order.
SIGN_COUNT = 12

#: The slugs in zodiac order. The site's own identifiers for the
#: twelve signs, used to check that the list really is in the order
#: the sign NUMBER claims it is in — the number is what a reader
#: sorts by, so an off-by-one in the parser would silently reorder
#: the horoscope.
SIGN_SLUGS = (
    "mesh", "brish", "mithun", "karkat", "singha", "kanya",
    "tula", "brischik", "dhanu", "makar", "kumbha", "meen",
)

#: Zero-width characters. The advice text opens with one, and it
#: would otherwise be the first thing in every string in the table.
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")

#: The trailing sentence of the advice, which is where the lucky
#: colour and the lucky number live:
#:
#:     आजको शुभ रंग गुलाबी हो भने शुभ अंक ७ रहेको छ।
#:
#: Matched loosely on the spacing, because the site is not
#: consistent about it: one sign has a space before the sentence and
#: another has two after the full stop.
_LUCKY_RE = re.compile(
    r"आजको\s*शुभ\s*रंग\s*(?P<colour>.+?)\s*हो\s*भने\s*शुभ\s*अंक\s*"
    r"(?P<number>[०-९0-9]+)\s*रहेको"
)


class RashifalError(Exception):
    """Anything that goes wrong fetching or parsing the horoscopes."""


class RashifalParseError(RashifalError):
    """The response was not what the parser expected."""


# =============================================================
# THE MODEL
# =============================================================

@dataclass(frozen=True)
class RashifalSign:
    """One sign, on one day."""

    sign: int
    slug: str
    name: str | None
    name_ne: str | None
    emoji: str | None
    initials: str | None
    advice: str | None
    lucky_colour: str | None
    lucky_number: str | None


# =============================================================
# PARSING
# =============================================================

def _clean(text: Any) -> str:
    """A string from the site, with the zero-width characters off."""
    if not isinstance(text, str):
        return ""
    return _ZERO_WIDTH.sub("", text).strip()


def _lucky(advice: str) -> tuple[str | None, str | None]:
    """
    The lucky colour and the lucky number, from the advice text.

    Both are None when the sentence is not the one this parser knows.
    A guess would be worse than nothing: a wrong lucky colour is
    printed as fact.
    """
    match = _LUCKY_RE.search(advice or "")
    if not match:
        return None, None
    return match.group("colour").strip(), match.group("number").strip()


def parse_rashifal(html: str) -> list[RashifalSign]:
    """
    The twelve signs, from the rashifal page.

    Raises `RashifalParseError` when the page carries no signs at
    all. A page that carries signs but is missing one of them is not
    an error: that sign is stored with the fields the site did give
    it, and the rest empty.
    """
    try:
        bootstrap = extract_bootstrap(html)
    except PanchangParseError as exc:
        # Re-raised as this module's own error, so a caller that
        # catches `RashifalParseError` catches a page with no JSON
        # island as well as a page with no signs in it. The two are
        # the same problem from out here: there is nothing to read.
        raise RashifalParseError(str(exc)) from exc

    data = bootstrap.get("data")
    block = data.get("rashifal") if isinstance(data, dict) else None
    entries = block.get("signs") if isinstance(block, dict) else None

    return _signs_from_entries(entries)


def parse_rashifal_day(html: str) -> list[RashifalSign]:
    """
    The twelve signs, from a PER-DAY page.

    The per-day page (`/date/2083-7-6`) carries the same twelve
    horoscopes, but at a different place in its JSON: `data.signs`,
    a flat list, rather than the `data.rashifal.signs` block the
    rashifal index page uses. The entries themselves are the same
    shape — slug, name in both languages, emoji, initials and the
    advice — so only the way in differs.

    This is what makes the UPCOMING DAYS possible. The rashifal index
    page only ever shows today, so a reader who asks for tomorrow's
    Scorpio has to be answered from the per-day page, and the per-day
    page is where tomorrow's horoscope is.

    Raises `RashifalParseError` when the page carries no signs.
    """
    try:
        bootstrap = extract_bootstrap(html)
    except PanchangParseError as exc:
        raise RashifalParseError(str(exc)) from exc

    data = bootstrap.get("data")
    entries = data.get("signs") if isinstance(data, dict) else None

    return _signs_from_entries(entries)


def _signs_from_entries(entries: Any) -> list[RashifalSign]:
    """
    Twelve signs, from the raw list the site serves.

    The two pages carry the same entries at two different places, so
    the conversion is done once, here.
    """
    if not isinstance(entries, list) or not entries:
        raise RashifalParseError("the page carries no rashifal signs")

    signs: list[RashifalSign] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue

        advice = _clean(entry.get("descNp")) or None
        colour, number = _lucky(advice or "")

        signs.append(
            RashifalSign(
                sign=index,
                slug=_clean(entry.get("slug")),
                name=_clean(entry.get("en")) or None,
                name_ne=_clean(entry.get("np")) or None,
                emoji=_clean(entry.get("emoji")) or None,
                initials=_clean(entry.get("initials")) or None,
                advice=advice,
                lucky_colour=colour,
                lucky_number=number,
            )
        )

    if len(signs) != SIGN_COUNT:
        logger.warning(
            "rashifal: the page carried %d sign(s), not %d. The horoscopes "
            "will be short. If the zodiac has grown a sign, say so.",
            len(signs), SIGN_COUNT,
        )
    else:
        slugs = tuple(sign.slug for sign in signs)
        if slugs != SIGN_SLUGS:
            logger.warning(
                "rashifal: the signs came back in an unexpected order (%s). "
                "The sign NUMBER no longer means what it did.",
                ", ".join(slugs),
            )

    return signs


# =============================================================
# STORING
# =============================================================

def rashifal_row(
    sign: RashifalSign, panchang_date: date, source_url: str
) -> dict[str, Any]:
    """
    The row for one sign, on one day.

    EVERY column is written on every pass, including `updated_at`.
    A bulk upsert (one POST for all twelve rows) leaves the columns
    a row does not mention NULL rather than falling back to the
    column default, so a column that is omitted from the payload is
    a column that gets wiped. `updated_at` has a NOT NULL default,
    and NULL there is a failed write.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "panchang_date": panchang_date.isoformat(),
        "sign": sign.sign,
        "sign_slug": sign.slug,
        "sign_name": sign.name,
        "sign_name_ne": sign.name_ne,
        "sign_emoji": sign.emoji,
        "initials": sign.initials,
        "advice": sign.advice,
        "lucky_colour": sign.lucky_colour,
        "lucky_number": sign.lucky_number,
        "source_url": source_url,
        "fetched_at": now,
        "updated_at": now,
    }


async def store_rashifal(
    db,
    signs: list[RashifalSign],
    panchang_date: date,
    source_url: str,
) -> bool:
    """
    Write the twelve signs for one day. True when they were stored.

    Upserts on (panchang_date, sign), so re-running the pass is free:
    the rows are refreshed in place and never duplicated.

    All twelve go in ONE request. They are written together or not
    at all, which is the right shape for a set that is read together
    — a page showing eleven of twelve horoscopes is a broken page,
    and a bulk write cannot half-succeed.

    Never raises: a failed write is a missing horoscope, not a
    reason to take the worker down.
    """
    if not signs:
        return False

    rows = [
        rashifal_row(sign, panchang_date, source_url) for sign in signs
    ]

    try:
        await db_call(
            lambda: db.table("daily_rashifal")
            .upsert(rows, on_conflict=RASHIFAL_CONFLICT)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a missing horoscope is not a crash
        logger.warning(
            "rashifal: could not store the horoscopes for %s: %s. The table "
            "is created by supabase/migrations/%s.",
            panchang_date.isoformat(), exc, RASHIFAL_MIGRATION,
        )
        return False

    return True
