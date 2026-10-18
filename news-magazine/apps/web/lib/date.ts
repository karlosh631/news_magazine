/**
 * Deterministic date formatting.
 *
 * Next.js renders on the server and then hydrates in the browser. If
 * both sides call Intl.DateTimeFormat with a locale, they can disagree,
 * because Node ships full ICU while some browsers do not:
 *
 *   Server: "२०२६ अक्टोबर १८, १०:२५"
 *   Client: "Oct 18, 2026, 10:25 AM"
 *
 * That mismatch is a React hydration error and it blanks the page. The
 * fix is to stop delegating to the platform's locale data and format the
 * digits and month names ourselves, so both sides produce byte-identical
 * output from the same input.
 */

const NEPALI_DIGITS = ["०", "१", "२", "३", "४", "५", "६", "७", "८", "९"];

const MONTHS_EN = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const MONTHS_NE = [
  "जनवरी", "फेब्रुअरी", "मार्च", "अप्रिल", "मे", "जुन",
  "जुलाई", "अगस्ट", "सेप्टेम्बर", "अक्टोबर", "नोभेम्बर", "डिसेम्बर",
];

export type SiteLanguage = "en" | "ne";

function toNepaliDigits(value: string): string {
  return value.replace(/[0-9]/g, (digit) => NEPALI_DIGITS[Number(digit)] ?? digit);
}

/**
 * Format an ISO timestamp as "18 October 2026, 10:25".
 *
 * Times are rendered in Asia/Kathmandu (UTC+05:45) regardless of where
 * the server or the browser happens to run, so a reader in London and a
 * reader in Delhi both see Nepal's local time — which is what a Nepal
 * news site should show.
 */
export function formatNewsDate(
  isoString: string | null | undefined,
  language: SiteLanguage = "en"
): string {
  if (!isoString) return "";

  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "";

  // Kathmandu is UTC+05:45 year-round (no DST).
  const kathmandu = new Date(date.getTime() + (5 * 60 + 45) * 60 * 1000);

  const day = kathmandu.getUTCDate();
  const monthIndex = kathmandu.getUTCMonth();
  const year = kathmandu.getUTCFullYear();
  const hours24 = kathmandu.getUTCHours();
  const minutes = kathmandu.getUTCMinutes();

  const month = language === "ne" ? MONTHS_NE[monthIndex] : MONTHS_EN[monthIndex];
  const day12 = hours24 % 12 === 0 ? 12 : hours24 % 12;
  const meridiem = hours24 < 12 ? "AM" : "PM";
  const time = `${day12}:${String(minutes).padStart(2, "0")} ${meridiem}`;

  const formatted = `${day} ${month} ${year}, ${time}`;
  return language === "ne" ? toNepaliDigits(formatted) : formatted;
}

/** Short relative age, e.g. "2h ago" / "२ घण्टा अघि". */
export function formatRelativeTime(
  isoString: string | null | undefined,
  language: SiteLanguage = "en"
): string {
  if (!isoString) return "";

  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "";

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return language === "ne" ? "अहिले" : "just now";

  const units: Array<[number, string, string]> = [
    [60, "m ago", "मिनेट अघि"],
    [3600, "h ago", "घण्टा अघि"],
    [86400, "d ago", "दिन अघि"],
  ];

  for (const [divisor, enSuffix, neSuffix] of units) {
    if (seconds < divisor * 60) {
      const value = Math.floor(seconds / divisor);
      const label = language === "ne" ? neSuffix : `${value}${enSuffix}`;
      return language === "ne" ? toNepaliDigits(label) : label;
    }
  }

  return formatNewsDate(isoString, language);
}