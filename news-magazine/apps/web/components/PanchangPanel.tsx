import Link from "next/link";

import { englishDate } from "@/lib/calendar-core";
import type { Panchang, PanchangDay, RashifalSign } from "@/lib/daily-info";

/**
 * The day's almanac, in full.
 *
 * WHAT IT IS
 * ----------
 * The panchang is the five limbs of the Nepali almanac — the tithi,
 * the nakshatra, the yoga and the karana, and the paksha the tithi
 * falls in — plus the sun's times and the windows the almanac calls
 * शुभ and अशुभ.
 *
 * It is the same information the Nepali calendar prints, for the
 * current day, on a page the reader can check against their own
 * almanac.
 *
 * THE DAYS AHEAD
 * --------------
 * A reader who asks for "tomorrow's tithi" is asking a real
 * question, and only today has a row in the table unless the worker
 * keeps the week ahead. So the worker does, and the row of buttons
 * under the heading is the days that really are there.
 *
 * The buttons are links, not a script: the day is in the URL, so
 * the reader can send tomorrow's horoscope to somebody and they
 * will see the same day rather than today's.
 *
 * NOTHING IN IT IS INVENTED
 * --------------------------
 * Every value here was fetched by the ingestion worker from the
 * source that publishes the almanac, and stored. If the worker has
 * not run, the panel says so rather than showing a blank where a
 * date should be — a panel with an empty field is honest, and a
 * panel with a computed-from-nothing date is a lie.
 */
export function PanchangPanel({
  panchang,
  signs = [],
  days = [],
  selectedDate,
  today,
  weekday,
  weekdayNe,
}: {
  panchang: Panchang | null;
  /** The twelve horoscopes for the day. */
  signs?: RashifalSign[];
  /** The days the almanac is stored for, oldest first. */
  days?: PanchangDay[];
  /** The day being shown, `YYYY-MM-DD`. */
  selectedDate?: string;
  /** Today in Kathmandu, `YYYY-MM-DD`. */
  today?: string;
  weekday: string;
  weekdayNe: string;
}) {
  const isToday = !selectedDate || !today || selectedDate === today;

  if (!panchang) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
          आजको पञ्चाङ
        </h2>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          Today&rsquo;s almanac has not been fetched yet. It is fetched once
          a day and stored, so this page fills itself the next time the
          worker runs.
        </p>
        {days.length ? <DayPicker days={days} selectedDate={selectedDate} today={today} /> : null}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ---- The day ---- */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
          {isToday ? "आजको पञ्चाङ" : "पञ्चाङ"}
        </p>
        <h2 className="mt-1 font-headline text-2xl font-bold text-gray-900 dark:text-gray-100">
          {panchang.bsLabel ?? panchang.date}
        </h2>
        {/*
          THE DATE, IN BOTH LANGUAGES.

          A Nepali reader reads the Bikram Sambat date first, and it is
          the one that is right for them. But the date they have to
          write on a form, or look up in a Western diary, is the
          Gregorian one — and the page is read by people whose English
          is the only language they have. So the weekday is given in
          both languages and the Gregorian date is given outright.
        */}
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {panchang.weekdayNe ?? weekdayNe}
          {panchang.weekday ? ` · ${panchang.weekday}` : ""}
          {panchang.date ? ` · ${englishDate(panchang.date)}` : ""}
        </p>

        {/* The days that can be picked. */}
        {days.length ? (
          <DayPicker days={days} selectedDate={selectedDate} today={today} />
        ) : null}

        {/* The five limbs. */}
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Limb label="तिथि (Tithi)" value={panchang.tithiFull ?? panchang.tithi} />
          <Limb label="नक्षत्र (Nakshatra)" value={panchang.nakshatra} />
          <Limb label="योग (Yoga)" value={panchang.yoga} />
          <Limb label="करण (Karana)" value={panchang.karana} />
          <Limb label="पक्ष (Paksha)" value={panchang.paksha} />
          <Limb label="चन्द्र कला (Moon phase)" value={panchang.moonPhase} />
        </dl>
      </section>

      {/* ---- The sun ---- */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
          सूर्य
        </h3>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Limb label="सूर्योदय (Sunrise)" value={panchang.sunrise} />
          <Limb label="सूर्यास्त (Sunset)" value={panchang.sunset} />
          <Limb
            label="दिनको लम्बाइ (Day length)"
            value={
              panchang.dayLengthMinutes !== null
                ? `${Math.floor(panchang.dayLengthMinutes / 60)} घण्टा ${
                    panchang.dayLengthMinutes % 60
                  } मिनेट`
                : null
            }
          />
        </dl>
      </section>

      {/* ---- The twelve signs ---- */}
      {signs.length === 0 ? (
        /*
          The source has not published this day's horoscope yet.

          It publishes a day's horoscope during that day, and until it
          does, its own per-day page repeats today's — so the copy we
          would have stored for this day is today's horoscope under
          this day's date. The same twelve sentences on seven different
          days, each with a different day of the week beside it, which
          is what the site used to show.

          So the day is not stored, and the page says so rather than
          showing a grid of nothing. The ALMANAC above it — the tithi,
          the nakshatra, the sun's times — is computed for the day and
          is correct, which is why the day is still worth offering.
        */
        <section className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
          <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
            राशिफल
          </h3>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            The horoscope for this day has not been published yet. It
            appears here as soon as the publisher writes it — usually
            during the day itself.
          </p>
        </section>
      ) : (
        <section className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
          <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
            {isToday ? "आजको राशिफल" : "राशिफल"}
          </h3>
          <p className="text-xs text-gray-400 dark:text-gray-500">
            the horoscope, for every sign
          </p>
          <ul className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {signs.map((sign) => (
              <li
                key={sign.sign}
                className="rounded-lg border border-gray-200 p-4 dark:border-gray-700"
              >
                {/* The name, in the source's own arrangement: the
                    symbol and the Nepali name on one line, the
                    English name at the far end of it. */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-baseline gap-2">
                    {sign.emoji ? (
                      <span aria-hidden="true" className="text-lg">
                        {sign.emoji}
                      </span>
                    ) : null}
                    <span className="text-base font-bold text-blue-700 dark:text-blue-400">
                      {sign.nameNe ?? sign.name ?? sign.slug ?? `Sign ${sign.sign}`}
                    </span>
                  </div>
                  {sign.name ? (
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      {sign.name}
                    </span>
                  ) : null}
                </div>
                {sign.initials ? (
                  <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                    {sign.initials}
                  </p>
                ) : null}
                {sign.advice ? (
                  <p className="mt-2 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
                    {withoutLuckySentence(sign.advice)}
                  </p>
                ) : null}
                {sign.luckyColour || sign.luckyNumber ? (
                  <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                    {sign.luckyColour ? (
                      <>
                        <span className="sr-only">Lucky colour: </span>
                        शुभ रंग {sign.luckyColour}
                      </>
                    ) : null}
                    {sign.luckyColour && sign.luckyNumber ? " · " : null}
                    {sign.luckyNumber ? (
                      <>
                        <span className="sr-only">Lucky number: </span>
                        शुभ अंक {sign.luckyNumber}
                      </>
                    ) : null}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---- The windows ---- */}
      <div className="grid gap-4 md:grid-cols-2">
        <WindowList
          title="शुभ समय"
          subtitle="the auspicious windows"
          entries={panchang.auspicious}
          tone="good"
        />
        <WindowList
          title="अशुभ समय"
          subtitle="the inauspicious windows"
          entries={panchang.inauspicious}
          tone="bad"
        />
      </div>
    </div>
  );
}

/**
 * The row of days the reader can pick.
 *
 * The days are links rather than buttons, and the day is in the
 * URL. A reader who sends somebody tomorrow's horoscope sends them
 * the URL, and they see the same day.
 *
 * The row scrolls sideways rather than wrapping: a week of days
 * wraps into two ragged lines on a phone, and a horizontal strip is
 * what a calendar of days looks like on a phone anyway.
 */
function DayPicker({
  days,
  selectedDate,
  today,
}: {
  days: PanchangDay[];
  selectedDate?: string;
  today?: string;
}) {
  return (
    <nav
      aria-label="दिन छान्नुहोस्"
      className="-mx-1 mt-4 flex gap-2 overflow-x-auto pb-2"
    >
      {days.map((day) => {
        const active = day.date === selectedDate;
        const isToday = day.date === today;
        const dayNumber = day.date.slice(-2);

        return (
          <Link
            key={day.date}
            href={`/category/rashifal?date=${day.date}`}
            scroll={false}
            aria-current={active ? "date" : undefined}
            /*
              48 pixels tall, and centred inside.

              The day strip is a row of small boxes that a finger has
              to pick from - the one control on this page that is
              genuinely touched - and at the size it was (two short
              lines of text, about 44 pixels) a mis-tap picked the
              wrong day, which is a day of the wrong horoscope.

              The Bikram Sambat date under the day number used to be
              10 pixels, which is below what Lighthouse counts as
              legible and below what the rest of the site's captions
              are set at.
            */
            className={`flex min-h-[48px] shrink-0 flex-col items-center justify-center rounded-lg border px-3 text-center transition-colors ${
              active
                ? "border-blue-600 bg-blue-600 text-white"
                : "border-gray-200 bg-white text-gray-700 hover:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-gray-500"
            }`}
          >
            <span className="block text-xs font-semibold">
              {isToday ? "आज" : dayNumber}
            </span>
            <span
              className={`mt-0.5 block text-xs ${
                active
                  ? "text-blue-50"
                  : "text-gray-400 dark:text-gray-500"
              }`}
            >
              {day.bsLabel ?? day.date}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

/**
 * The advice without the lucky sentence at its end.
 *
 * The source publishes the advice and then, in the same paragraph, the
 * lucky colour and the lucky number:
 *
 *   "...। आजको शुभ रंग गुलाबी हो भने शुभ अंक ७ रहेको छ।"
 *
 * The row keeps that sentence, because the words are the source's and
 * the extraction of the colour and the number reads out of them. But
 * the card shows the colour and the number as their own line, so the
 * sentence has to come off the advice or the reader sees it twice.
 *
 * The stored text is untouched; this is a display decision.
 */
function withoutLuckySentence(advice: string): string {
  return advice.replace(/\s*आजको\s*शुभ\s*रंग[\s\S]*$/u, "").trim();
}

function Limb({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm font-medium text-gray-900 dark:text-gray-100">
        {value ?? "—"}
      </dd>
    </div>
  );
}

function WindowList({
  title,
  subtitle,
  entries,
  tone,
}: {
  title: string;
  subtitle: string;
  entries: string[];
  tone: "good" | "bad";
}) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
      <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
        {title}
      </h3>
      <p className="text-xs text-gray-400 dark:text-gray-500">{subtitle}</p>
      {entries.length ? (
        <ul className="mt-3 space-y-1.5">
          {entries.map((entry, index) => (
            <li
              key={`${entry}-${index}`}
              className={`flex items-baseline justify-between gap-3 rounded px-2 py-1 text-sm ${
                tone === "good"
                  ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                  : "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"
              }`}
            >
              <span>{entry}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-gray-400 dark:text-gray-500">
          Not published for today.
        </p>
      )}
    </section>
  );
}
