import { NextResponse } from "next/server";

import {
  allDays,
  bsKey,
  parseBsDate,
} from "@/lib/calendar-core";
import {
  fetchCalendarDay,
  fetchCalendarMonth,
  fetchCalendarYear,
} from "@/lib/calendar";

/**
 * The Nepali calendar, for the widget on the Rashifal page.
 *
 * WHY AN API AT ALL
 * -----------------
 * The calendar is a grid of days, and the reader moves through it:
 * back to last month, forward to next month, into the week, and into
 * one day's detail. None of that needs a page load — the page behind
 * the calendar is the Rashifal page, with the almanac and the twelve
 * horoscopes on it, and a reader who clicks a date should not lose
 * their place on it.
 *
 * So the widget asks this route for the month it wants and for the day
 * it wants, and the page it is on stays where it is.
 *
 * WHAT IT SERVES
 * ---------------
 *   GET /api/calendar?year=2083&month=7
 *       the month asked for, the month before it and the month after
 *       it, and today's detail
 *
 *   GET /api/calendar?date=2083-7-7
 *       that one day, with its panchang
 *
 * The same data is read by the page itself, so the first render needs
 * no request at all — this route is for the moves the reader makes.
 */
export const dynamic = "force-dynamic";

/**
 * The cache.
 *
 * An hour, because a day of the Bikram Sambat calendar does not
 * change within the hour and the month does not change at all until
 * the month turns. The browser is told to hold it for five minutes
 * only: the widget is on a page that is otherwise live, and a reader
 * who clicks back to the month they were on should not wait for the
 * publisher.
 */
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400",
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const date = url.searchParams.get("date");
  const year = url.searchParams.get("year");
  const month = url.searchParams.get("month");

  // One day.
  if (date) {
    const parsed = parseBsDate(date);
    if (!parsed) {
      return NextResponse.json(
        { error: "A date must be written as YYYY-M-D, e.g. 2083-7-7." },
        { status: 400, headers: CACHE_HEADERS }
      );
    }

    const detail = await fetchCalendarDay(parsed.year, parsed.month, parsed.day);
    if (!detail) {
      return NextResponse.json(
        { error: "That day could not be read.", day: null },
        { status: 200, headers: CACHE_HEADERS }
      );
    }

    return NextResponse.json(
      { day: detail, key: bsKey(detail) },
      { headers: CACHE_HEADERS }
    );
  }

  // A year.
  if (year && !month) {
    const yearBs = Number(year);
    if (!Number.isFinite(yearBs)) {
      return NextResponse.json(
        { error: "The year must be a number." },
        { status: 400, headers: CACHE_HEADERS }
      );
    }

    const months = await fetchCalendarYear(yearBs);
    if (!months || months.length === 0) {
      return NextResponse.json(
        { error: "That year could not be read.", months: [] },
        { status: 200, headers: CACHE_HEADERS }
      );
    }

    return NextResponse.json(
      { months, days: allDays(months), requested: { year: yearBs } },
      { headers: CACHE_HEADERS }
    );
  }

  // A month.
  if (!year || !month) {
    return NextResponse.json(
      { error: "Ask for a month with ?year=2083&month=7, a year with ?year=2083, or a day with ?date=2083-7-7." },
      { status: 400, headers: CACHE_HEADERS }
    );
  }

  const yearBs = Number(year);
  const monthBs = Number(month);
  if (!Number.isFinite(yearBs) || !Number.isFinite(monthBs)) {
    return NextResponse.json(
      { error: "The year and the month must be numbers." },
      { status: 400, headers: CACHE_HEADERS }
    );
  }

  const view = await fetchCalendarMonth(yearBs, monthBs);
  if (!view) {
    return NextResponse.json(
      { error: "The calendar could not be read.", months: [] },
      { status: 200, headers: CACHE_HEADERS }
    );
  }

  return NextResponse.json(
    {
      months: view.months,
      current: view.current,
      today: view.today,
      days: allDays(view.months),
      requested: { year: yearBs, month: monthBs },
    },
    { headers: CACHE_HEADERS }
  );
}
