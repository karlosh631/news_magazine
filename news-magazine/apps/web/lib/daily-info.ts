import { unstable_cache } from "next/cache";

import { createPublicSupabaseClient } from "@/lib/supabase/server";

/**
 * The day's almanac, for the header and for the Rashifal page.
 *
 * WHAT IS IN IT
 * -------------
 *   - today, in Gregorian and in Bikram Sambat
 *   - the tithi, the nakshatra, the yoga and the karana
 *   - sunrise, sunset and the length of the day
 *   - the windows the panchang calls शुभ and अशुभ
 *
 * WHY THE PANCHANG COMES FROM THE DATABASE
 * -----------------------------------------
 * The Bikram Sambat date, the tithi and the nakshatra are computed
 * from the Nepali solar and lunar calendars, and getting them right
 * needs the source that publishes them. A wrong date is worse than no
 * date: a site that says "५ माघ २०८३" on the wrong day is not a site
 * that says nothing.
 *
 * So the almanac is fetched once a day by the ingestion worker, from
 * the source that publishes it, and stored. The site reads the stored
 * row.
 *
 * The almanac is NOT an article. It does not come through the article
 * pipeline: it is not deduplicated against articles, it is not
 * categorised, and it never becomes a card.
 *
 * NOTHING HERE MAY BREAK A PAGE
 * -----------------------------
 * Every part of this is optional. The table may not exist yet (the
 * migration is not applied) and the row may not be there yet (the
 * worker has not run). In each of those cases the caller shows what
 * it has and stops — it never throws and never holds a page up.
 */

export type Panchang = {
  /** The Gregorian day this row is FOR, `YYYY-MM-DD`. */
  date: string;
  /** The Bikram Sambat year, e.g. 2083. */
  bsYear: number | null;
  /** The Bikram Sambat month, 1-based: Baishakh is 1, Chaitra is 12. */
  bsMonth: number | null;
  /** The Bikram Sambat day, 1-based. */
  bsDay: number | null;
  /** The date as the source writes it, e.g. "कार्तिक ५, २०८३". */
  bsLabel: string | null;
  /** The weekday, in English and Nepali. */
  weekday: string | null;
  weekdayNe: string | null;
  /** The bare tithi, e.g. "एकादशी". */
  tithi: string | null;
  /** The whole tithi, e.g. "असोज शुक्ल एकादशी". */
  tithiFull: string | null;
  /** शुक्ल (waxing) or कृष्ण (waning). */
  paksha: string | null;
  nakshatra: string | null;
  yoga: string | null;
  karana: string | null;
  moonPhase: string | null;
  /** The wall-clock times as the source prints them, in Nepali. */
  sunrise: string | null;
  sunset: string | null;
  /** How long the day is, in minutes. */
  dayLengthMinutes: number | null;
  /** The शुभ and अशुभ windows. */
  auspicious: string[];
  inauspicious: string[];
  /** Where the row came from. */
  sourceUrl: string | null;
};

export type DailyInfo = {
  /** Today in Kathmandu, `YYYY-MM-DD`. */
  today: string;
  /** The weekday, in English and Nepali. */
  weekday: string;
  weekdayNe: string;
  panchang: Panchang | null;
};

/** Today's date in Kathmandu. Kathmandu is UTC+05:45 all year. */
export function kathmanduToday(now: Date = new Date()): string {
  const kathmandu = new Date(now.getTime() + (5 * 60 + 45) * 60 * 1000);
  return kathmandu.toISOString().slice(0, 10);
}

const WEEKDAYS_EN = [
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
];
const WEEKDAYS_NE = [
  "आइतबार", "सोमबार", "मंगलबार", "बुधबार", "बिहिबार", "शुक्रबार", "शनिबार",
];

function asString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text ? text : null;
}

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (typeof entry === "string") return entry.trim();
      if (entry && typeof entry === "object") {
        const record = entry as Record<string, unknown>;
        return (
          asString(record.label) ??
          asString(record.name) ??
          asString(record.time) ??
          asString(record.text) ??
          ""
        );
      }
      return "";
    })
    .filter((entry) => entry.length > 0);
}

/**
 * The almanac for a day.
 *
 * Null when the table is not there or the row is not there yet. Both
 * are ordinary states: the migration may not be applied, and the
 * worker may not have run yet today.
 */
async function readPanchang(date: string): Promise<Panchang | null> {
  try {
    const supabase = createPublicSupabaseClient();
    const { data, error } = await supabase
      .from("daily_panchang")
      .select(
        "panchang_date, bikram_sambat_year, bikram_sambat_month, bikram_sambat_day, " +
          "bikram_sambat_label, weekday, weekday_np, tithi, tithi_full, paksha, " +
          "nakshatra, yoga, karana, moon_phase, sunrise, sunset, day_length_minutes, " +
          "auspicious_times, inauspicious_times, source_url"
      )
      .eq("panchang_date", date)
      .maybeSingle();

    if (error || !data) return null;

    const row = data as unknown as Record<string, unknown>;
    return {
      date: asString(row.panchang_date) ?? date,
      bsYear: asNumber(row.bikram_sambat_year),
      bsMonth: asNumber(row.bikram_sambat_month),
      bsDay: asNumber(row.bikram_sambat_day),
      bsLabel: asString(row.bikram_sambat_label),
      weekday: asString(row.weekday),
      weekdayNe: asString(row.weekday_np),
      tithi: asString(row.tithi),
      tithiFull: asString(row.tithi_full),
      paksha: asString(row.paksha),
      nakshatra: asString(row.nakshatra),
      yoga: asString(row.yoga),
      karana: asString(row.karana),
      moonPhase: asString(row.moon_phase),
      sunrise: asString(row.sunrise),
      sunset: asString(row.sunset),
      dayLengthMinutes: asNumber(row.day_length_minutes),
      auspicious: asList(row.auspicious_times),
      inauspicious: asList(row.inauspicious_times),
      sourceUrl: asString(row.source_url),
    };
  } catch {
    // The table is not there yet. Not an error.
    return null;
  }
}

/**
 * The almanac, cached.
 *
 * The header reads this on every page render. The Supabase client
 * deliberately never caches — a cached read of anything but public data
 * is a cached read of a credential — so the cache is here instead,
 * around the one query.
 *
 * FIVE MINUTES, NOT AN HOUR
 * -------------------------
 * The almanac for a day does not change during that day, so an hour
 * would be generous for the CONTENT. But the cache holds the RESULT,
 * and the result is "there is no row for today" for the whole of the
 * night before the worker has run. An hour-long cache on that answer
 * means the page says "not fetched yet" for an hour after it has been
 * fetched — which is exactly what it did, and it looked like the
 * feature was broken rather than slow.
 *
 * Five minutes is one query every five minutes for a row that is one
 * per day, and it means a freshly fetched almanac is on the page
 * within five minutes.
 */
const readCachedPanchang = unstable_cache(
  async (date: string) => readPanchang(date),
  ["daily-panchang"],
  { revalidate: 300 }
);

/**
 * Everything the header shows.
 *
 * Never throws. A missing almanac costs the caller the almanac, and
 * nothing else.
 */
export async function getDailyInfo(now: Date = new Date()): Promise<DailyInfo> {
  const today = kathmanduToday(now);

  // The weekday is computed from the Kathmandu date, not from the
  // server's own timezone: a reader in London and a reader in Delhi
  // must see the same day, and at 18:00 UTC the two are already on
  // different dates.
  const kathmandu = new Date(now.getTime() + (5 * 60 + 45) * 60 * 1000);
  const weekdayIndex = kathmandu.getUTCDay();

  const panchang = await readCachedPanchang(today);

  return {
    today,
    weekday: WEEKDAYS_EN[weekdayIndex] ?? "",
    weekdayNe: WEEKDAYS_NE[weekdayIndex] ?? "",
    panchang,
  };
}

/**
 * The almanac for a day, for the Rashifal page.
 *
 * The page is addressed by date, so the day can be any day the worker
 * has fetched — not only today.
 */
export async function getPanchangFor(
  date: string
): Promise<Panchang | null> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  return readCachedPanchang(date);
}

/**
 * A day the almanac is stored for.
 *
 * The short form, for the row of day buttons a reader picks a day
 * from: which day it is, what the source calls it, and what the
 * almanac says about it.
 */
export type PanchangDay = {
  /** The Gregorian day, `YYYY-MM-DD`. */
  date: string;
  /** The date as the source writes it, e.g. "कार्तिक ५, २०८३". */
  bsLabel: string | null;
  /** The weekday, in English and in Nepali. */
  weekday: string | null;
  weekdayNe: string | null;
  /** The tithi, for the tooltip and for the summary line. */
  tithi: string | null;
};

/**
 * The days the almanac is stored for, from `from` onwards.
 *
 * WHY THE DAYS ARE LISTED RATHER THAN GUESSED
 * -------------------------------------------
 * A reader who asks for "tomorrow" has to be given a day that
 * actually exists. The Bikram Sambat calendar's month lengths are
 * not regular — 29 to 32 days, and a month that occasionally
 * repeats — so the seventh day from today cannot be worked out by
 * adding seven to anything. The worker walks the source's own
 * day-to-day links and stores what it finds, and this list is the
 * result: the days that really are there.
 *
 * The list is therefore the truth about what the page can show. A
 * day that is not in it is a day the page must not offer.
 */
async function readPanchangDays(
  from: string,
  limit: number
): Promise<PanchangDay[]> {
  try {
    const supabase = createPublicSupabaseClient();
    const { data, error } = await supabase
      .from("daily_panchang")
      .select(
        "panchang_date, bikram_sambat_label, weekday, weekday_np, tithi"
      )
      .gte("panchang_date", from)
      .order("panchang_date", { ascending: true })
      .limit(limit);

    if (error || !data) return [];

    return ((data ?? []) as unknown[]).map((raw) => {
      const row = raw as Record<string, unknown>;
      return {
        date: asString(row.panchang_date) ?? "",
        bsLabel: asString(row.bikram_sambat_label),
        weekday: asString(row.weekday),
        weekdayNe: asString(row.weekday_np),
        tithi: asString(row.tithi),
      };
    }).filter((day) => day.date.length > 0);
  } catch {
    // The table is not there yet. Not an error.
    return [];
  }
}

const readCachedPanchangDays = unstable_cache(
  async (from: string, limit: number) => readPanchangDays(from, limit),
  ["daily-panchang-days"],
  { revalidate: 300 }
);

/**
 * The days the almanac is stored for, from `from` onwards.
 *
 * Empty when the table is not there or the worker has not run. Both
 * are ordinary states, and neither is a reason to fail a page.
 */
export async function getPanchangDays(
  from: string,
  limit = 14
): Promise<PanchangDay[]> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from)) return [];
  return readCachedPanchangDays(from, limit);
}

export type RashifalSign = {
  /** The sign, 1 to 12: 1 is मेष (Aries), 12 is मीन (Pisces). */
  sign: number;
  /** The source's own slug for the sign. */
  slug: string | null;
  /** The sign's name, in English and in Nepali. */
  name: string | null;
  nameNe: string | null;
  /** The zodiac symbol and the letter initials the source prints. */
  emoji: string | null;
  initials: string | null;
  /** Today's advice for this sign, verbatim. */
  advice: string | null;
  /** The lucky colour and the lucky number. */
  luckyColour: string | null;
  luckyNumber: string | null;
};

/**
 * The twelve horoscopes for a day.
 *
 * This is the राशिफल proper - the twelve signs, each with its own
 * advice - and it is a DIFFERENT thing from the panchang above. The
 * panchang is one row for the day; this is twelve rows for the day,
 * one per sign, because a sign is a thing with its own name, its own
 * advice and its own lucky number, and somebody asking for "today's
 * Scorpio" is asking a different question from somebody asking for
 * "today's tithi".
 *
 * Null when the table is not there or the day has not been fetched.
 * Both are ordinary states.
 */
async function readRashifal(date: string): Promise<RashifalSign[]> {
  try {
    const supabase = createPublicSupabaseClient();
    const { data, error } = await supabase
      .from("daily_rashifal")
      .select(
        "sign, sign_slug, sign_name, sign_name_ne, sign_emoji, initials, " +
          "advice, lucky_colour, lucky_number"
      )
      .eq("panchang_date", date)
      .order("sign", { ascending: true });

    if (error || !data) return [];

    return ((data ?? []) as unknown[]).map((raw) => {
      const row = raw as Record<string, unknown>;
      return {
        sign: asNumber(row.sign) ?? 0,
        slug: asString(row.sign_slug),
        name: asString(row.sign_name),
        nameNe: asString(row.sign_name_ne),
        emoji: asString(row.sign_emoji),
        initials: asString(row.initials),
        advice: asString(row.advice),
        luckyColour: asString(row.lucky_colour),
        luckyNumber: asString(row.lucky_number),
      };
    });
  } catch {
    return [];
  }
}

const readCachedRashifal = unstable_cache(
  async (date: string) => readRashifal(date),
  ["daily-rashifal"],
  { revalidate: 300 }
);

/**
 * The twelve horoscopes for a day, for the Rashifal page.
 */
export async function getRashifalFor(date: string): Promise<RashifalSign[]> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
  return readCachedRashifal(date);
}
