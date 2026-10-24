import "server-only";

import type { RashifalSign } from "@/lib/daily-info";

/**
 * The rashifal for a period rather than for a day.
 *
 * THE FOUR PERIODS A READER CAN ASK FOR
 * -------------------------------------
 * The source's own Rashifal page carries four tabs, and the reader
 * asked for all four:
 *
 *     दैनिक     /rashifal            today
 *     साप्ताहिक  /rashifal/weekly     this week
 *     मासिक     /rashifal/monthly    this month
 *     वार्षिक    /rashifal/yearly     this year
 *
 * We only ever read the first one. The other three are separate pages
 * with the same twelve signs and a longer prediction for each, so the
 * reader was shown a page that could answer one of their four
 * questions.
 *
 * WHERE THE DATA COMES FROM
 * -------------------------
 * The daily horoscope is the site's OWN copy: the ingestion reads
 * /rashifal once a day and stores twelve rows in `daily_rashifal`, so
 * the daily tab needs no request at all and cannot break when the
 * publisher is slow.
 *
 * The other three periods are not stored - there is no table for them
 * and a schema change is the operator's to make - so they are read
 * from the source when the page is built and cached for an hour. One
 * request an hour for a page a reader asks for by name is a fair
 * price, and the cache means a burst of readers is still one request.
 */

export type RashifalPeriod = "daily" | "weekly" | "monthly" | "yearly";

export const RASHIFAL_PERIODS: Array<{
  key: RashifalPeriod;
  label: string;
  path: string;
}> = [
  { key: "daily", label: "दैनिक", path: "/rashifal" },
  { key: "weekly", label: "साप्ताहिक", path: "/rashifal/weekly" },
  { key: "monthly", label: "मासिक", path: "/rashifal/monthly" },
  { key: "yearly", label: "वार्षिक", path: "/rashifal/yearly" },
];

/** The source's own address for a period. */
export const PERIOD_SOURCE_URL: Record<RashifalPeriod, string> = {
  daily: "https://hamropatro.com/rashifal",
  weekly: "https://hamropatro.com/rashifal/weekly",
  monthly: "https://hamropatro.com/rashifal/monthly",
  yearly: "https://hamropatro.com/rashifal/yearly",
};

/**
 * The page's JSON island.
 *
 * The source is a Next.js app that server-renders its data into a
 * single JSON block rather than fetching it, so this is the
 * machine-readable form of the page. It is the same block the
 * ingestion parses (app/services/rashifal.py in the Python service).
 */
const BOOTSTRAP_RE =
  /<script[^>]*id="hp-bootstrap"[^>]*type="application\/json"[^>]*>([\s\S]*?)<\/script>/;

type SourceSign = {
  slug?: string;
  np?: string;
  en?: string;
  emoji?: string;
  initials?: string;
  descNp?: string;
  descEn?: string;
};

/**
 * The twelve signs of a period, read from the source.
 *
 * Empty when the source cannot be read. A missing week is an ordinary
 * state: the page still shows the panchang and the daily horoscope.
 */
export async function fetchRashifalPeriod(
  period: RashifalPeriod
): Promise<RashifalSign[]> {
  if (period === "daily") return [];

  const url = PERIOD_SOURCE_URL[period];

  let html: string;
  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      },
      // An hour is long enough: a weekly prediction does not change
      // from one minute to the next, and the daily tab is the site's
      // own copy anyway.
      next: { revalidate: 3600 },
    });
    if (!response.ok) return [];
    html = await response.text();
  } catch {
    return [];
  }

  const match = BOOTSTRAP_RE.exec(html);
  if (!match) return [];

  let payload: { data?: { rashifal?: { signs?: SourceSign[] } } };
  try {
    payload = JSON.parse(match[1] ?? "{}") as typeof payload;
  } catch {
    return [];
  }

  const signs = payload.data?.rashifal?.signs ?? [];

  return signs.map((sign, index) => ({
    sign: index + 1,
    slug: sign.slug ?? null,
    name: sign.en ?? null,
    nameNe: sign.np ?? null,
    emoji: sign.emoji ?? null,
    initials: sign.initials ?? null,
    advice: sign.descNp ?? sign.descEn ?? null,
    luckyColour: null,
    luckyNumber: null,
  }));
}

/**
 * The period a reader asked for.
 *
 * Anything that is not one of the four is the daily one, which is the
 * page's own default.
 */
export function readPeriod(raw: string | null | undefined): RashifalPeriod {
  if (raw === "weekly" || raw === "monthly" || raw === "yearly") return raw;
  return "daily";
}
