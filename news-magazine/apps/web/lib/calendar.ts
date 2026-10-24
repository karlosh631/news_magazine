import "server-only";

import { unstable_cache } from "next/cache";

import {
  allDays,
  bsKey,
  formatBsDate,
  monthOf,
  normaliseDetail,
  normaliseMonth,
  parseBootstrap,
  type CalendarDay,
  type CalendarDayDetail,
  type CalendarMonth,
} from "@/lib/calendar-core";

/**
 * The Nepali calendar, read from the publisher that publishes it.
 *
 * WHY IT IS READ AT ALL
 * ---------------------
 * The Bikram Sambat calendar is not something to compute. Its months
 * run 29 to 32 days, its leap years are irregular, and a month
 * occasionally repeats — so a calendar that guessed would eventually
 * tell a Nepali reader that today is the wrong day, which is worse
 * than showing no calendar at all.
 *
 * The source server-renders its calendar as a JSON island, so the
 * reading is a parse and nothing more. The site keeps no copy of its
 * own: the almanac (the panchang and the twelve horoscopes) is
 * stored, because it is read once a day and the dates in it are the
 * site's own; the CALENDAR is read live and cached for an hour,
 * because it is a grid of days and a grid is cheap to re-read and
 * expensive to keep.
 *
 * WHAT HAPPENS WHEN THE SOURCE IS UNREACHABLE
 * -------------------------------------------
 * The reader gets the page without the calendar, and a line saying so.
 * Nothing throws, nothing is cached as an empty calendar, and the
 * almanac — which is the site's own copy — is unaffected.
 *
 * WHY THE CACHE IS AN HOUR
 * -------------------------
 * A day of the Bikram Sambat calendar does not change within the hour.
 * The month's cells do not change at all until the month turns, and
 * the day's detail — the tithi, the sunrise, the events — changes
 * when the day does. An hour is generous for the content and cheap
 * for the publisher: one request an hour for a page a reader asks for
 * by name, and a burst of readers is still one request.
 */

const CALENDAR_BASE = "https://www.hamropatro.com";

/** The reader proxy, used only when the publisher refuses this address. */
const READER_BASE = "https://r.jina.ai";

/** How long a read may take. */
const READ_TIMEOUT_MS = 20_000;

const BROWSER_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "ne,en-US;q=0.9,en;q=0.8",
};

/**
 * A read of the source.
 *
 * The publisher refuses datacentre addresses for some paths, and a
 * refusal is not a failure: the page is read through the reader proxy
 * instead, which fetches it from an address the publisher does not
 * refuse. The proxy returns markdown by default and the calendar is
 * server-rendered HTML, so it is asked for HTML.
 */
async function readPage(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      headers: BROWSER_HEADERS,
      redirect: "follow",
      signal: AbortSignal.timeout(READ_TIMEOUT_MS),
    });
    if (response.ok) return await response.text();
    if (response.status < 400) return null;
  } catch {
    // Fall through to the reader.
  }

  // The publisher refused this address (or the request timed out). Read
  // it from one that has not been refused.
  try {
    const response = await fetch(`${READER_BASE}/${url}`, {
      headers: {
        ...BROWSER_HEADERS,
        "x-respond-with": "html",
      },
      redirect: "follow",
      signal: AbortSignal.timeout(READ_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return await response.text();
  } catch {
    return null;
  }
}

/**
 * The three months around a month, and the day the calendar calls
 * today.
 */
export interface CalendarMonthView {
  /** Every month that was read: the one before, the one asked for, the one after. */
  months: CalendarMonth[];
  /** The month that was asked for. */
  current: CalendarMonth | null;
  /** Today, with its panchang. */
  today: CalendarDayDetail | null;
  /** Every day of every month that was read, in order. */
  days: CalendarDay[];
}

async function readCalendarMonth(
  yearBs: number,
  monthBs: number
): Promise<CalendarMonthView | null> {
  const url = `${CALENDAR_BASE}/calendar/${yearBs}/${monthBs}`;
  const html = await readPage(url);
  if (!html) return null;

  const payload = parseBootstrap(html);
  if (!payload) return null;

  const data = (payload.data ?? {}) as Record<string, unknown>;
  const calendar = (data.calendar ?? {}) as Record<string, unknown>;
  const rawMonths = Array.isArray(calendar.initialMonths)
    ? calendar.initialMonths
    : [];

  const months: CalendarMonth[] = [];
  for (const entry of rawMonths) {
    const month = normaliseMonth(entry);
    if (month) months.push(month);
  }
  if (months.length === 0) return null;

  // The source says which of the three it was asked about, but it is
  // matched on the year and month as well, so a change in that index
  // cannot point the page at the wrong month.
  const index = Number(calendar.initialIndex);
  const byIndex = Number.isFinite(index) ? months[index] : undefined;
  const byName = monthOf(months, { yearBs, monthBs });
  const current = byName ?? byIndex ?? months[0] ?? null;

  const today = normaliseDetail(calendar.todayDetail);

  return { months, current, today, days: allDays(months) };
}

const readCachedMonth = unstable_cache(
  async (yearBs: number, monthBs: number) => readCalendarMonth(yearBs, monthBs),
  ["calendar-month"],
  { revalidate: 3600 }
);

/**
 * The calendar for the month that is running now.
 *
 * The source's own home page carries the same calendar island its
 * calendar page does, so the month can be read without knowing which
 * Bikram Sambat month it is — which is the one thing the site cannot
 * work out for itself.
 *
 * It is the fallback for the case where the worker has not yet stored
 * today's almanac: without the stored row there is no Bikram Sambat
 * year and month to ask for, and the calendar would be the one thing
 * the page could not show.
 */
async function readCurrentCalendar(): Promise<CalendarMonthView | null> {
  const url = `${CALENDAR_BASE}/`;
  const html = await readPage(url);
  if (!html) return null;

  const payload = parseBootstrap(html);
  if (!payload) return null;

  const data = (payload.data ?? {}) as Record<string, unknown>;
  const calendar = (data.calendar ?? {}) as Record<string, unknown>;
  const rawMonths = Array.isArray(calendar.initialMonths)
    ? calendar.initialMonths
    : [];

  const months: CalendarMonth[] = [];
  for (const entry of rawMonths) {
    const month = normaliseMonth(entry);
    if (month) months.push(month);
  }
  if (months.length === 0) return null;

  const index = Number(calendar.initialIndex);
  const current =
    (Number.isFinite(index) ? months[index] : undefined) ??
    months[0] ??
    null;
  const today = normaliseDetail(calendar.todayDetail);

  return { months, current, today, days: allDays(months) };
}

const readCachedCurrent = unstable_cache(
  async () => readCurrentCalendar(),
  ["calendar-current"],
  { revalidate: 3600 }
);

/**
 * The calendar for the month that is running now.
 */
export async function fetchCurrentCalendar(): Promise<CalendarMonthView | null> {
  try {
    return await readCachedCurrent();
  } catch {
    return null;
  }
}

/**
 * The calendar for a month: the month asked for, the one before it and
 * the one after it, plus today.
 *
 * Null when the source cannot be read. A calendar that cannot be read
 * is an ordinary state — the page shows the almanac and nothing else.
 */
export async function fetchCalendarMonth(
  yearBs: number,
  monthBs: number
): Promise<CalendarMonthView | null> {
  if (!Number.isFinite(yearBs) || !Number.isFinite(monthBs)) return null;
  if (yearBs < 2000 || yearBs > 2200) return null;
  if (monthBs < 1 || monthBs > 12) return null;
  try {
    return await readCachedMonth(yearBs, monthBs);
  } catch {
    return null;
  }
}

async function readCalendarDay(
  yearBs: number,
  monthBs: number,
  dayBs: number
): Promise<CalendarDayDetail | null> {
  const url = `${CALENDAR_BASE}/date/${formatBsDate(yearBs, monthBs, dayBs)}`;
  const html = await readPage(url);
  if (!html) return null;

  const payload = parseBootstrap(html);
  if (!payload) return null;

  const data = (payload.data ?? {}) as Record<string, unknown>;
  return normaliseDetail(data.detail);
}

const readCachedDay = unstable_cache(
  async (yearBs: number, monthBs: number, dayBs: number) =>
    readCalendarDay(yearBs, monthBs, dayBs),
  ["calendar-day"],
  { revalidate: 3600 }
);

/**
 * One day, with its panchang: the tithi, the yoga, the karana, the
 * nakshatra, the sunrise, the sunset, the moon and what happens on it.
 *
 * Null when the day cannot be read. The widget shows what it has.
 */
export async function fetchCalendarDay(
  yearBs: number,
  monthBs: number,
  dayBs: number
): Promise<CalendarDayDetail | null> {
  if (!Number.isFinite(yearBs) || !Number.isFinite(monthBs)) return null;
  if (!Number.isFinite(dayBs) || dayBs < 1 || dayBs > 32) return null;
  try {
    return await readCachedDay(yearBs, monthBs, dayBs);
  } catch {
    return null;
  }
}

/**
 * The address of a day on the source, for the reader who wants the
 * whole page.
 */
export function sourceDayUrl(
  yearBs: number,
  monthBs: number,
  dayBs: number
): string {
  return `${CALENDAR_BASE}/date/${formatBsDate(yearBs, monthBs, dayBs)}`;
}

/**
 * The key of a day, for the address bar and for the widget's own
 * state.
 */
export { bsKey };
