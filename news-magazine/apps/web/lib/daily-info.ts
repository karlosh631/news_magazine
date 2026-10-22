import { unstable_cache } from "next/cache";

import { createPublicSupabaseClient } from "@/lib/supabase/server";

/**
 * The day's information, for the strip under the header.
 *
 * WHAT IS IN IT
 * -------------
 *   - today, in Gregorian and in Bikram Sambat
 *   - the tithi and the nakshatra
 *   - sunrise and sunset
 *   - the weather in Kathmandu
 *
 * WHY THE PANCHANG COMES FROM THE DATABASE
 * -----------------------------------------
 * The Bikram Sambat date, the tithi and the nakshatra are computed
 * from the Nepali solar calendar, and getting them right needs a
 * table of the calendar's month lengths for every year. A wrong
 * table is worse than no date at all - a site that says "५ माघ
 * २०८३" on the wrong day is not a site that says nothing.
 *
 * So the almanac is fetched once a day by the ingestion worker, from
 * the source that publishes it, and stored. The header reads the
 * stored row.
 *
 * The strip is NOT the article feed, so it does not come through the
 * article pipeline: it is not deduplicated against articles, it is
 * not categorised, and it never becomes a card.
 *
 * NOTHING HERE MAY BREAK A PAGE
 * -----------------------------
 * Every part of this is optional. The table may not exist yet (the
 * migration is not applied), the row may not be there yet (the worker
 * has not run), and the weather service may be down. In each of
 * those cases the strip shows what it has and stops - it never throws
 * and never holds a page up.
 */

/** Kathmandu, for the weather. */
const KATHMANDU = { latitude: 27.7172, longitude: 85.324 };

/**
 * How long the weather is cached for.
 *
 * Half an hour. The weather does not change in the time it takes to
 * notice, and this is read on every page, so an uncached call would
 * be a call per page view.
 */
const WEATHER_REVALIDATE_SECONDS = 1800;

export type PanchangRow = {
  /** The Gregorian date the row is for, `YYYY-MM-DD`. */
  date: string;
  /** The Bikram Sambat date, in Nepali, e.g. "५ कार्तिक २०८३". */
  bsDate: string | null;
  /** The tithi, e.g. "शुभल एकादशी". */
  tithi: string | null;
  /** The nakshatra, e.g. "रानीपक्ष". */
  nakshatra: string | null;
  /** Sunrise, in Asia/Kathmandu. */
  sunrise: string | null;
  /** Sunset, in Asia/Kathmandu. */
  sunset: string | null;
};

export type Weather = {
  /** Degrees Celsius. */
  temperature: number | null;
  /** A short English description, e.g. "Clear". */
  description: string | null;
  /** A short Nepali description, e.g. "स्पष्ट". */
  descriptionNe: string | null;
  /** An icon key: "clear" | "cloud" | "rain" | "snow" | "storm" | "fog". */
  icon: "clear" | "cloud" | "rain" | "snow" | "storm" | "fog" | null;
};

export type DailyInfo = {
  /** Today in Kathmandu, `YYYY-MM-DD`. */
  today: string;
  /** The weekday, in English and Nepali. */
  weekday: string;
  weekdayNe: string;
  panchang: PanchangRow | null;
  weather: Weather | null;
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

/**
 * The WMO weather codes, in the three shapes the strip needs.
 *
 * The codes are the international standard, so this table is not a
 * guess - but the Nepali words are, and a wrong word in a header is
 * visible on every page of the site, so each one is the ordinary
 * Nepali for the thing.
 */
const WEATHER_CODES: Record<
  number,
  { en: string; ne: string; icon: Weather["icon"] }
> = {
  0: { en: "Clear", ne: "स्पष्ट", icon: "clear" },
  1: { en: "Mostly clear", ne: "प्रायः स्पष्ट", icon: "clear" },
  2: { en: "Partly cloudy", ne: "आंशिक बदल", icon: "cloud" },
  3: { en: "Overcast", ne: "बदल", icon: "cloud" },
  45: { en: "Fog", ne: "हावा", icon: "fog" },
  48: { en: "Rime fog", ne: "हावा", icon: "fog" },
  51: { en: "Light drizzle", ne: "हल्का झिरपिर", icon: "rain" },
  53: { en: "Drizzle", ne: "झिरपिर", icon: "rain" },
  55: { en: "Heavy drizzle", ne: "गह्रौं झिरपिर", icon: "rain" },
  56: { en: "Freezing drizzle", ne: "जम्ने झिरपिर", icon: "rain" },
  57: { en: "Freezing drizzle", ne: "जम्ने झिरपिर", icon: "rain" },
  61: { en: "Light rain", ne: "हल्का पानी", icon: "rain" },
  63: { en: "Rain", ne: "पानी", icon: "rain" },
  65: { en: "Heavy rain", ne: "गह्रौं पानी", icon: "rain" },
  66: { en: "Freezing rain", ne: "जम्ने पानी", icon: "rain" },
  67: { en: "Freezing rain", ne: "जम्ने पानी", icon: "rain" },
  71: { en: "Light snow", ne: "हल्का हिम", icon: "snow" },
  73: { en: "Snow", ne: "हिम", icon: "snow" },
  75: { en: "Heavy snow", ne: "गह्रौं हिम", icon: "snow" },
  77: { en: "Snow grains", ne: "हिम", icon: "snow" },
  80: { en: "Rain showers", ne: "पानी", icon: "rain" },
  81: { en: "Rain showers", ne: "पानी", icon: "rain" },
  82: { en: "Violent rain showers", ne: "तीव्र पानी", icon: "rain" },
  85: { en: "Snow showers", ne: "हिम", icon: "snow" },
  86: { en: "Snow showers", ne: "हिम", icon: "snow" },
  95: { en: "Thunderstorm", ne: "आँधी", icon: "storm" },
  96: { en: "Thunderstorm with hail", ne: "आँधी", icon: "storm" },
  99: { en: "Thunderstorm with hail", ne: "आँधी", icon: "storm" },
};

/**
 * What the weather is doing, from the WMO code.
 *
 * Exported so it can be tested: the codes are a table of a hundred
 * entries, and a typo in one of them is a wrong word in the header of
 * every page on the site.
 */
export function describeWeatherCode(code: number): {
  en: string;
  ne: string;
  icon: Weather["icon"];
} | null {
  return WEATHER_CODES[code] ?? null;
}

/**
 * The almanac for today.
 *
 * Null when the table is not there or the row is not there yet. Both
 * are ordinary states: the migration may not be applied, and the
 * worker may not have run yet today.
 */
async function readPanchang(today: string): Promise<PanchangRow | null> {
  try {
    const supabase = createPublicSupabaseClient();
    const { data, error } = await supabase
      .from("daily_panchang")
      .select("date, bs_date, tithi, nakshatra, sunrise, sunset")
      .eq("date", today)
      .maybeSingle();

    if (error || !data) return null;

    const row = data as Record<string, unknown>;
    return {
      date: String(row.date ?? today),
      bsDate: row.bs_date ? String(row.bs_date) : null,
      tithi: row.tithi ? String(row.tithi) : null,
      nakshatra: row.nakshatra ? String(row.nakshatra) : null,
      sunrise: row.sunrise ? String(row.sunrise) : null,
      sunset: row.sunset ? String(row.sunset) : null,
    };
  } catch {
    // The table is not there yet. Not an error.
    return null;
  }
}

/**
 * The almanac, cached.
 *
 * The strip is in the layout, so this runs on every page render. The
 * Supabase client deliberately never caches - a cached read of
 * anything but public data is a cached read of a credential - so the
 * cache is here instead, around the one query.
 *
 * The almanac for a day does not change during that day, so an hour
 * is generous.
 */
const readCachedPanchang = unstable_cache(
  async (today: string) => readPanchang(today),
  ["daily-panchang"],
  { revalidate: 3600 }
);

/**
 * The weather in Kathmandu.
 *
 * Open-Meteo, which needs no key and no account. A weather API that
 * needs a key is a weather API that stops working when the key
 * expires, and a header that stops working is a broken site.
 */
async function readWeather(): Promise<Weather | null> {
  try {
    const url = new URL("https://api.open-meteo.com/v1/forecast");
    url.searchParams.set("latitude", String(KATHMANDU.latitude));
    url.searchParams.set("longitude", String(KATHMANDU.longitude));
    url.searchParams.set(
      "current",
      "temperature_2m,weather_code,is_day"
    );
    url.searchParams.set("timezone", "Asia/Kathmandu");

    const response = await fetch(url.toString(), {
      next: { revalidate: WEATHER_REVALIDATE_SECONDS },
    });
    if (!response.ok) return null;

    const payload = (await response.json()) as {
      current?: { temperature_2m?: number; weather_code?: number };
    };

    const current = payload.current;
    if (!current || typeof current.temperature_2m !== "number") return null;

    const code = current.weather_code ?? 0;
    const described = WEATHER_CODES[code];

    return {
      temperature: Math.round(current.temperature_2m),
      description: described?.en ?? null,
      descriptionNe: described?.ne ?? null,
      icon: described?.icon ?? "clear",
    };
  } catch {
    return null;
  }
}

/**
 * Everything the header strip shows.
 *
 * Never throws. A missing almanac and a missing weather each cost the
 * strip one line, and the rest of the header is unaffected.
 */
export async function getDailyInfo(now: Date = new Date()): Promise<DailyInfo> {
  const today = kathmanduToday(now);

  // The weekday is computed from the Kathmandu date, not from the
  // server's own timezone: a reader in London and a reader in Delhi
  // must see the same day, and at 18:00 UTC the two are already on
  // different dates.
  const kathmandu = new Date(now.getTime() + (5 * 60 + 45) * 60 * 1000);
  const weekdayIndex = kathmandu.getUTCDay();

  const [panchang, weather] = await Promise.all([
    readCachedPanchang(today),
    readWeather(),
  ]);

  return {
    today,
    weekday: WEEKDAYS_EN[weekdayIndex] ?? "",
    weekdayNe: WEEKDAYS_NE[weekdayIndex] ?? "",
    panchang,
    weather,
  };
}
