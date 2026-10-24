/**
 * The Nepali calendar: the shape of it, and the arithmetic over it.
 *
 * WHY THIS IS PURE
 * ----------------
 * Everything here is a plain function over plain data — no fetch, no
 * database, no clock. That is what makes it testable without a browser
 * and without the network, which is the only kind of test this
 * repository's suite can run.
 *
 * The fetching lives in lib/calendar.ts, which is server-only; the
 * widget lives in components/NepaliCalendar.tsx, which is a client
 * component. This file is the contract between them.
 *
 * WHERE THE DATA COMES FROM
 * -------------------------
 * The Bikram Sambat calendar is not something to compute: its months
 * are 29 to 32 days long, its leap years are irregular, and a month
 * occasionally repeats. Getting it wrong means telling a Nepali reader
 * that today is the wrong day, which is worse than saying nothing.
 *
 * So the calendar is read from the publisher that publishes it, which
 * server-renders it as a JSON island, and the shapes below are the
 * reading of that island. Nothing here guesses a date.
 */

/** One thing that happens on a day. */
export interface CalendarEvent {
  /** The event's name, in Nepali. */
  title: string;
  /** The same event's name, in English, when the source gives one. */
  titleEn: string | null;
  /** Whether it is a public holiday (as opposed to an observance). */
  isHoliday: boolean;
}

/** One day of the calendar. */
export interface CalendarDay {
  /** The Bikram Sambat year, e.g. 2083. */
  yearBs: number;
  /** The Bikram Sambat month, 1-based: Baishakh is 1, Chaitra is 12. */
  monthBs: number;
  /** The Bikram Sambat day, 1-based. */
  dayBs: number;
  /** The day of the month as the source writes it, e.g. "७". */
  dayBsNp: string;
  /** The Gregorian day of the month as the source writes it, e.g. "२४". */
  dayAdNp: string;
  /** The Gregorian day this cell is, `YYYY-MM-DD`. */
  dateAd: string;
  /** 0 is Sunday. */
  dayOfWeek: number;
  /** Whether the cell belongs to the month it is shown in. */
  inMonth: boolean;
  isToday: boolean;
  isHoliday: boolean;
  isWeekendHoliday: boolean;
  /** The tithi, short: "त्रयोदशी". */
  tithiShort: string | null;
  /** What happens on this day. */
  events: CalendarEvent[];
}

/** One month of the calendar, with the cells around it. */
export interface CalendarMonth {
  yearBs: number;
  monthBs: number;
  /** "कार्तिक" */
  monthNameNe: string;
  /** "Kartik" */
  monthNameEn: string;
  /** "२०८३" */
  yearBsNe: string;
  daysInMonth: number;
  /**
   * The cells, in order, from the Sunday on or before the first of the
   * month. There are always enough of them to hold the whole month.
   */
  days: CalendarDay[];
  /** The month before this one, in Bikram Sambat terms. */
  prev: { year: number; month: number };
  /** The month after this one. */
  next: { year: number; month: number };
}

/** The five limbs of the panchang, in the words the source uses. */
export interface PanchangText {
  tithi: string | null;
  yoga: string | null;
  karana: string | null;
  nakshatra: string | null;
}

/** A day, with everything the source knows about it. */
export interface CalendarDayDetail extends CalendarDay {
  /** The tithi, the yoga, the karana and the nakshatra, by name. */
  panchanga: PanchangText;
  /** "०६:०८" */
  sunrise: string | null;
  /** "१७:२६" */
  sunset: string | null;
  /** The phase of the moon, with its name. */
  moonPhase: { emoji: string | null; nameNe: string | null; nameEn: string | null };
  /** The Nepali Sambat date, as the source prints it. */
  nepalSambat: string | null;
  /** "शनिवार" */
  weekdayNe: string | null;
  /** "कार्तिक" */
  monthNe: string | null;
  /** "२०८३" */
  yearBsNe: string | null;
}

/** The three views the calendar can be read in. */
export type CalendarView = "day" | "week" | "month";

export const CALENDAR_VIEWS: Array<{ key: CalendarView; label: string }> = [
  { key: "day", label: "दिन" },
  { key: "week", label: "हप्ता" },
  { key: "month", label: "महिना" },
];

/**
 * The source's JSON island.
 *
 * The source is a Next.js application that server-renders its data into
 * one JSON block rather than fetching it, so this is the
 * machine-readable form of the page. It is the same block the Python
 * ingestion parses for the almanac.
 */
const BOOTSTRAP_RE =
  /<script[^>]*id="hp-bootstrap"[^>]*type="application\/json"[^>]*>([\s\S]*?)<\/script>/;

/**
 * The island, as an object.
 *
 * Null when the page has no island, or when the island is not JSON —
 * which is what a redesign looks like from here. A caller that gets
 * null shows what it has and stops; it never throws.
 */
export function parseBootstrap(html: string): Record<string, unknown> | null {
  const match = BOOTSTRAP_RE.exec(html);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1] ?? "{}");
    if (!parsed || typeof parsed !== "object") return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

function asString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const text = value.trim();
    return text ? text : null;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asBoolean(value: unknown): boolean {
  return value === true || value === "true" || value === 1;
}

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

/**
 * The events of a day.
 *
 * The source carries them twice: a list of items, and a joined title.
 * The list is the useful one, because a day can have four of them and
 * the joined title runs them together with slashes.
 */
function readEvents(raw: unknown): CalendarEvent[] {
  if (!raw || typeof raw !== "object") return [];
  const record = raw as Record<string, unknown>;
  const items = Array.isArray(record.items) ? record.items : [];
  const events: CalendarEvent[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    const title = asString(entry.titleNp) ?? asString(entry.titleEn);
    if (!title) continue;
    events.push({
      title,
      titleEn: asString(entry.titleEn),
      isHoliday: asBoolean(entry.isHoliday),
    });
  }
  return events;
}

/**
 * One day, from the source's own object.
 *
 * Null when the object is not a day — a missing day is an ordinary
 * state (the calendar is read from a publisher that may be slow, and
 * a cell it did not describe is a cell with no detail), never a reason
 * to fail.
 */
export function normaliseDay(raw: unknown): CalendarDay | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;

  const yearBs = asNumber(record.year_bs);
  const monthBs = asNumber(record.month_bs);
  const dayBs = asNumber(record.day_bs);
  if (yearBs === null || monthBs === null || dayBs === null) return null;

  const yearAd = asNumber(record.year_ad);
  const monthAd = asNumber(record.month_ad);
  const dayAd = asNumber(record.day_ad);

  return {
    yearBs,
    monthBs,
    dayBs,
    dayBsNp: asString(record.dayBsNp) ?? String(dayBs),
    dayAdNp: asString(record.dayAdNp) ?? (dayAd === null ? "" : String(dayAd)),
    dateAd:
      yearAd === null || monthAd === null || dayAd === null
        ? ""
        : `${yearAd}-${pad2(monthAd)}-${pad2(dayAd)}`,
    dayOfWeek: asNumber(record.dayOfWeek) ?? 0,
    inMonth: asBoolean(record.inMonth),
    isToday: asBoolean(record.isToday),
    isHoliday: asBoolean(record.isHoliday),
    isWeekendHoliday: asBoolean(record.isWeekendHoliday),
    tithiShort: asString(record.tithiShort),
    events: readEvents(record.patroEvent),
  };
}

/**
 * One month, from the source's own object.
 */
export function normaliseMonth(raw: unknown): CalendarMonth | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;

  const yearBs = asNumber(record.yearBs);
  const monthBs = asNumber(record.monthBs);
  if (yearBs === null || monthBs === null) return null;

  const rawDays = Array.isArray(record.days) ? record.days : [];
  const days: CalendarDay[] = [];
  for (const entry of rawDays) {
    const day = normaliseDay(entry);
    if (day) days.push(day);
  }

  const prev = (record.prev ?? {}) as Record<string, unknown>;
  const next = (record.next ?? {}) as Record<string, unknown>;

  return {
    yearBs,
    monthBs,
    monthNameNe: asString(record.monthNameNp) ?? "",
    monthNameEn: asString(record.monthNameEn) ?? "",
    yearBsNe: asString(record.yearBsNp) ?? "",
    daysInMonth: asNumber(record.daysInMonth) ?? days.length,
    days,
    prev: {
      year: asNumber(prev.year) ?? yearBs,
      month: asNumber(prev.month) ?? monthBs,
    },
    next: {
      year: asNumber(next.year) ?? yearBs,
      month: asNumber(next.month) ?? monthBs,
    },
  };
}

/**
 * A day with its panchang, from the source's own object.
 *
 * The detail page and the calendar page carry the same day in two
 * different shapes, and this reads either of them.
 */
export function normaliseDetail(raw: unknown): CalendarDayDetail | null {
  const day = normaliseDay(raw);
  if (!day) return null;
  const record = raw as Record<string, unknown>;

  const text = (record.panchangaText ?? {}) as Record<string, unknown>;
  const moon = (record.moonPhase ?? {}) as Record<string, unknown>;
  const moonName = (moon.name ?? {}) as Record<string, unknown>;
  const sambat = (record.nepalSambat ?? {}) as Record<string, unknown>;

  return {
    ...day,
    panchanga: {
      tithi: asString(text.tithi),
      yoga: asString(text.yoga),
      karana: asString(text.karana),
      nakshatra: asString(text.nakshatra),
    },
    sunrise: asString(record.sunriseNp),
    sunset: asString(record.sunsetNp),
    moonPhase: {
      emoji: asString(moon.emoji),
      nameNe: asString(moonName.np),
      nameEn: asString(moonName.en),
    },
    nepalSambat: asString(sambat.label),
    weekdayNe: asString(record.weekdayNp),
    monthNe: asString(record.monthNp),
    yearBsNe: asString(record.yearBsNp),
  };
}

/** The key a day is addressed by: "2083-7-7". */
export function bsKey(day: {
  yearBs: number;
  monthBs: number;
  dayBs: number;
}): string {
  return `${day.yearBs}-${day.monthBs}-${day.dayBs}`;
}

/**
 * Today in Kathmandu, `YYYY-MM-DD`.
 *
 * Kathmandu is UTC+05:45 all year, and the difference is not a round
 * number of hours — which is exactly why the offset is written out
 * rather than left to the machine's timezone. A reader in London and a
 * reader in Delhi must see the same day, and at 18:00 UTC the two are
 * already on different dates.
 */
export function kathmanduDay(now: Date = new Date()): string {
  const kathmandu = new Date(now.getTime() + (5 * 60 + 45) * 60 * 1000);
  return kathmandu.toISOString().slice(0, 10);
}

/** The months, in English. */
export const ENGLISH_MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/**
 * A Gregorian date, written the way an English reader writes it:
 * "24 October 2026".
 *
 * It is built from the ISO string rather than from `toLocaleDateString`,
 * because the calendar is rendered on the server AND in the browser and
 * the two must agree exactly — a date that differs between them is a
 * hydration mismatch, and a calendar whose day changes under the
 * reader's finger is worse than no calendar at all.
 */
export function englishDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!match) return null;
  const month = ENGLISH_MONTHS[Number(match[2]) - 1];
  if (!month) return null;
  return `${Number(match[3])} ${month} ${match[1]}`;
}

/** The day of the month, in English numerals: "24". */
export function englishDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  return match ? String(Number(match[3])) : null;
}

/** The weekdays, in English, Sunday first. */
export const WEEKDAYS_EN = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

/**
 * "2083-7-7" as its three parts.
 *
 * Null for anything that is not that shape. The key arrives in a URL,
 * so it is the reader's to write, and a reader who writes a wrong one
 * gets the default view rather than an error.
 */
export function parseBsDate(
  value: string | null | undefined
): { year: number; month: number; day: number } | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 32) return null;
  return { year, month, day };
}

/**
 * The three parts as the key the source's own URLs use.
 */
export function formatBsDate(
  year: number,
  month: number,
  day: number
): string {
  return `${year}-${month}-${day}`;
}

/**
 * Every day of a month, in order, from the source's own cells.
 *
 * The source draws the grid itself and knows how many cells the month
 * needs, so its cells are used as they are — the site does not compute
 * where a month starts.
 */
export function monthCells(month: CalendarMonth | null): CalendarDay[] {
  if (!month) return [];
  return month.days;
}

/**
 * The seven days of the week a day falls in.
 *
 * The Nepali week starts on Sunday, and the source's cells are laid out
 * that way, so the week is the run of cells that begins on the Sunday
 * on or before the day.
 *
 * The days come from every month that was read, because a week can
 * straddle two of them — and it does: the first and last days of a
 * Nepali month are routinely in the month before and the month after.
 */
export function weekOf(
  days: readonly CalendarDay[],
  anchor: { yearBs: number; monthBs: number; dayBs: number }
): CalendarDay[] {
  const sorted = [...days].sort(
    (a, b) =>
      a.yearBs - b.yearBs || a.monthBs - b.monthBs || a.dayBs - b.dayBs
  );
  const index = sorted.findIndex((day) => bsKey(day) === bsKey(anchor));
  if (index === -1) return [];

  // Walk back to the Sunday. `dayOfWeek` is 0 for Sunday.
  const offset = sorted[index]!.dayOfWeek % 7;
  const start = Math.max(0, index - offset);
  return sorted.slice(start, start + 7);
}

/**
 * Every day of the months that were read, without duplicates.
 *
 * The three months the source returns overlap at their edges, and the
 * week view is built from the whole set, so the days are keyed and
 * de-duplicated here.
 */
export function allDays(
  months: readonly CalendarMonth[]
): CalendarDay[] {
  const byKey = new Map<string, CalendarDay>();
  for (const month of months) {
    for (const day of month.days) {
      const key = bsKey(day);
      if (!byKey.has(key)) byKey.set(key, day);
    }
  }
  return [...byKey.values()].sort(
    (a, b) =>
      a.yearBs - b.yearBs || a.monthBs - b.monthBs || a.dayBs - b.dayBs
  );
}

/**
 * The month a day falls in, out of the months that were read.
 */
export function monthOf(
  months: readonly CalendarMonth[],
  day: { yearBs: number; monthBs: number }
): CalendarMonth | null {
  for (const month of months) {
    if (month.yearBs === day.yearBs && month.monthBs === day.monthBs) {
      return month;
    }
  }
  return null;
}
