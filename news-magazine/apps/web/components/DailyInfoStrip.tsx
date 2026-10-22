import { getDailyInfo, type DailyInfo } from "@/lib/daily-info";

/**
 * The day's information, in the space under the header.
 *
 * WHAT IT IS FOR
 * --------------
 * The header had a wide empty band between the navigation and the
 * search box, and this is what fills it: what day it is, what the
 * almanac says, and what the weather is doing. A Nepali reader
 * checks the date and the tithi before they read the news, and the
 * site was making them go somewhere else for it.
 *
 * IT IS NOT A CARD
 * ----------------
 * It is a strip, not a story. There is no picture, no headline and no
 * click target, because none of it is an article and it must never
 * look like one.
 *
 * NOTHING IN IT CAN BREAK A PAGE
 * ------------------------------
 * The almanac row may not exist (the migration is not applied), the
 * worker may not have run yet today, and the weather service may be
 * down. Each of those costs the strip a line and nothing else. The
 * date and the weather are always there, because the date is
 * computed and the weather is a separate call.
 */
export async function DailyInfoStrip() {
  let info: DailyInfo | null = null;
  try {
    info = await getDailyInfo();
  } catch {
    info = null;
  }

  // No date means no strip. That cannot happen - the date is
  // computed - but a strip that renders nothing is better than one
  // that renders a broken half.
  if (!info) return null;

  const { panchang, weather } = info;

  return (
    <div
      className="border-b border-gray-200 bg-gray-50/80 dark:border-gray-800 dark:bg-gray-900/60"
      // The date is the point of the strip, and it is what a reader
      // copies, so it is selectable text and not an image.
      aria-label="Today's date, almanac and weather"
    >
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 text-[11px] sm:px-6 lg:px-8">
        {/* ---- The date ---- */}
        <span className="font-medium text-gray-700 dark:text-gray-300">
          {panchang?.bsDate ? (
            <>
              {panchang.bsDate}, {info.weekday}
            </>
          ) : (
            <>
              {new Date(`${info.today}T00:00:00Z`).toLocaleDateString("en-GB", {
                day: "numeric",
                month: "long",
                year: "numeric",
                timeZone: "UTC",
              })}
              , {info.weekday}
            </>
          )}
        </span>

        {/* ---- The almanac ---- */}
        {panchang?.tithi ? (
          <span className="text-gray-500 dark:text-gray-400">
            <span className="sr-only">Today&rsquo;s tithi: </span>
            {panchang.tithi}
            {panchang.nakshatra ? ` · ${panchang.nakshatra}` : ""}
          </span>
        ) : null}

        {/* ---- Sunrise and sunset ---- */}
        {panchang?.sunrise ? (
          <span className="hidden text-gray-500 dark:text-gray-400 sm:inline">
            <span className="sr-only">Sunrise: </span>
            {panchang.sunrise}
            {panchang.sunset ? ` – ${panchang.sunset}` : ""}
          </span>
        ) : null}

        {/* ---- The weather ---- */}
        {weather ? (
          <span className="ml-auto flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
            <span aria-hidden="true">{WEATHER_ICON[weather.icon ?? "clear"]}</span>
            {weather.temperature !== null ? (
              <span className="tabular-nums">{weather.temperature}°C</span>
            ) : null}
            {weather.description ? (
              <span className="hidden sm:inline">{weather.description}</span>
            ) : null}
            <span className="hidden sm:inline">· काठमाडौं</span>
          </span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A glyph per kind of sky.
 *
 * These are characters rather than images on purpose: an icon font
 * would be another request, another thing to load, and another way
 * for the strip to look broken while the page is still loading. The
 * glyphs are in the reader's own font and they are already there.
 */
const WEATHER_ICON: Record<string, string> = {
  clear: "☀",
  cloud: "☁",
  rain: "☂",
  snow: "❄",
  storm: "⚡",
  fog: "🌫",
};
