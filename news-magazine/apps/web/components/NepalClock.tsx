"use client";

import { useEffect, useState } from "react";

/**
 * The time in Nepal.
 *
 * The site's readers are in Nepal, and the site's clock was theirs
 * browser's clock - which is right for them, and wrong for nobody,
 * except that a news site that never says what time it is leaves the
 * reader doing the arithmetic against every story's timestamp. The
 * reader asked for the live Nepal time on every page.
 *
 * IT IS THE READER'S OWN CLOCK, NOT THE SERVER'S
 * ----------------------------------------------
 * The time is rendered on the client from the browser's clock, so it
 * is the reader's own time and it ticks without the page being
 * re-rendered. The zone is NEPAL'S, not the reader's: Kathmandu is
 * UTC+5:45, and a reader abroad still wants to know what time it is
 * in the country the news is from.
 *
 * The server's render is a placeholder - the same text with the hour
 * and minute filled in on the client - so the page is never empty and
 * never lies about a time it cannot know.
 */

const NEPAL_TIME_ZONE = "Asia/Kathmandu";

/** The clock, as it reads in Kathmandu. */
function nepalNow(): string {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: NEPAL_TIME_ZONE,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date());
  } catch {
    // A runtime without the zone database: Kathmandu is a fixed
    // offset from UTC all year round, so the arithmetic is exact.
    const now = new Date();
    const kathmandu = new Date(now.getTime() + (5 * 60 + 45) * 60_000);
    const hours = String(kathmandu.getUTCHours()).padStart(2, "0");
    const minutes = String(kathmandu.getUTCMinutes()).padStart(2, "0");
    return `${hours}:${minutes}`;
  }
}

export function NepalClock() {
  const [time, setTime] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => setTime(nepalNow());
    tick();
    // Every half minute: a clock that ticks every second is a clock
    // the reader reads, and this one is for knowing roughly what time
    // it is.
    const timer = window.setInterval(tick, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <span
      className="hidden items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-gray-600 tabular-nums dark:text-gray-300 lg:inline-flex"
      title="नेपाली समय (Nepal time)"
      aria-label={`Nepal time${time ? `, ${time}` : ""}`}
    >
      <span aria-hidden="true" className="text-gray-400 dark:text-gray-500">
        NPT
      </span>
      {time ?? "--:--"}
    </span>
  );
}
