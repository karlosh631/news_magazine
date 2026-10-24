"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import {
  allDays,
  bsKey,
  CALENDAR_VIEWS,
  ENGLISH_MONTHS,
  englishDate,
  englishDay,
  WEEKDAYS_EN,
  kathmanduDay,
  monthOf,
  weekOf,
  type CalendarDay,
  type CalendarDayDetail,
  type CalendarMonth,
  type CalendarView,
} from "@/lib/calendar-core";
import {
  focusableWithin,
  focusThenHide,
  isEscapeKey,
  lockBodyScroll,
  nextFocusIndex,
  showThenRestore,
} from "@/lib/story-dialog";

/**
 * The Nepali calendar, on the Rashifal page.
 *
 * WHAT IT IS
 * ----------
 * A month of the Bikram Sambat calendar, the week inside it, and the
 * day itself — one widget with three readings, because a reader
 * arrives with one of three questions:
 *
 *   दिन     what does today look like?
 *   हप्ता   what does this week look like?
 *   महिना   what does this month look like?
 *
 * Clicking a date opens that date, with its panchang — the tithi, the
 * yoga, the karana and the nakshatra — the sunrise and the sunset,
 * the phase of the moon, and what happens on it.
 *
 * WHY IT IS NOT COMPUTED HERE
 * ---------------------------
 * The Bikram Sambat calendar has months of 29 to 32 days, irregular
 * leap years, and months that occasionally repeat. A calendar that
 * guessed would eventually tell a Nepali reader that today is the
 * wrong day, which is worse than no calendar at all. So the grid is
 * read from the publisher that publishes it — see lib/calendar.ts —
 * and this component only arranges it.
 *
 * WHY IT IS A WIDGET AND NOT A LINK
 * ---------------------------------
 * The reader moves through the calendar without leaving the page. The
 * Rashifal page is the almanac and the twelve horoscopes, and a
 * reader who clicks a date should not lose their place on it to look
 * at a date.
 *
 * WHY IT FOLLOWS THE DAY
 * ----------------------
 * The calendar is read for the day it is on, so it has to move when
 * the day moves. A tab left open across midnight Kathmandu time would
 * otherwise show yesterday's grid with yesterday's "today" on it. The
 * widget checks the Kathmandu date once a minute and re-reads the
 * month when it has turned.
 */

/** The source's own address for a day. */
function sourceDayUrl(day: {
  yearBs: number;
  monthBs: number;
  dayBs: number;
}): string {
  return `https://www.hamropatro.com/date/${day.yearBs}-${day.monthBs}-${day.dayBs}`;
}

/** The weekday headers, in Nepali, Sunday first. */
const WEEKDAY_HEADERS = [
  "आइतबार",
  "सोमबार",
  "मंगलबार",
  "बुधबार",
  "बिहिबार",
  "शुक्रबार",
  "शनिबार",
];

/** The same weekdays, in English. */
const WEEKDAY_HEADERS_EN = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

/**
 * The Gregorian months a Bikram Sambat month covers: "Oct/Nov 2026".
 *
 * A Nepali month is not a Western one — कार्तिक runs from the middle
 * of October to the end of November — so the reader who wants to know
 * which Western month to look in needs both names. It is built from
 * the month's own cells, so it is a fact about this month and not a
 * table somebody has to keep up to date.
 */
function gregorianRange(month: CalendarMonth | null): string | null {
  if (!month) return null;
  const days = month.days.filter((day) => day.inMonth);
  if (days.length === 0) return null;

  const first = /^(\d{4})-(\d{2})-/.exec(days[0]?.dateAd ?? "");
  const last = /^(\d{4})-(\d{2})-/.exec(days[days.length - 1]?.dateAd ?? "");
  if (!first || !last) return null;

  const firstMonth = ENGLISH_MONTHS[Number(first[2]) - 1];
  const lastMonth = ENGLISH_MONTHS[Number(last[2]) - 1];
  if (!firstMonth || !lastMonth) return null;

  const year = first[1];
  return firstMonth.slice(0, 3) === lastMonth.slice(0, 3)
    ? `${firstMonth.slice(0, 3)} ${year}`
    : `${firstMonth.slice(0, 3)}/${lastMonth.slice(0, 3)} ${year}`;
}

/**
 * What a cell says to a screen reader.
 *
 * The visible cell is a picture: a corner date, a day, a tithi and
 * whatever happens that day. A reader who cannot see it needs the
 * same thing in words, and the events are the part that matters —
 * they are why somebody is looking at this cell at all.
 */
function cellLabel(day: CalendarDay, month: CalendarMonth): string {
  const parts = [
    `${day.dayBsNp} ${month.monthNameNe}`,
    englishDate(day.dateAd) ?? "",
    day.tithiShort ?? "",
    day.events.map((event) => event.title).join(", "),
  ];
  return parts.filter(Boolean).join(", ");
}

/**
 * Open a day from the year grid.
 *
 * The year grid is a different component from the month grid, and it
 * does not have the month grid's `onOpenDay` to call. So the day is
 * opened through the same route the rest of the widget uses: a custom
 * event on the section, which the widget listens for.
 */
function onOpenDayOf(
  month: CalendarMonth,
  day: CalendarDay,
  trigger: HTMLElement | null
): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent("nepali-calendar:open-day", {
      detail: { day, trigger },
    })
  );
}

export function NepaliCalendar({
  months,
  today,
  initialDay,
  period,
}: {
  /** The months that were read: the one before, the one asked for, the one after. */
  months: CalendarMonth[];
  /** Today, with its panchang. */
  today: CalendarDayDetail | null;
  /** The day that is open when the page arrives. */
  initialDay: CalendarDayDetail | null;
  /** The rashifal period the page is showing, kept in the address. */
  period?: string;
}) {
  const router = useRouter();
  const [view, setView] = useState<CalendarView>("month");
  const [read, setRead] = useState<CalendarMonth[]>(months);
  const [yearMonths, setYearMonths] = useState<CalendarMonth[]>([]);
  const [selected, setSelected] = useState<string | null>(
    initialDay ? bsKey(initialDay) : null
  );
  const [detail, setDetail] = useState<CalendarDayDetail | null>(initialDay);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const panelRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const triggerRef = useRef<HTMLElement | null>(null);

  /*
    THE MONTH BEING LOOKED AT
    -------------------------
    It starts at the month today is in, and the arrows move it. It is
    NOT the month the page was rendered for, because a reader who
    clicks "next month" and then has the page re-rendered must not be
    thrown back where they started.
  */
  const [cursor, setCursor] = useState(() => {
    const month = monthOf(months, {
      yearBs: initialDay?.yearBs ?? today?.yearBs ?? 2083,
      monthBs: initialDay?.monthBs ?? today?.monthBs ?? 7,
    });
    return {
      year: month?.yearBs ?? initialDay?.yearBs ?? today?.yearBs ?? 2083,
      month: month?.monthBs ?? initialDay?.monthBs ?? today?.monthBs ?? 7,
    };
  });

  const days = useMemo(() => allDays(read), [read]);

  const visible = useMemo(
    () => monthOf(read, { yearBs: cursor.year, monthBs: cursor.month }),
    [read, cursor]
  );

  /*
    THE DAYS THE THREE VIEWS SHOW
    ------------------------------
    The day view is the selected day; the week view is the seven days
    of the week the selected day falls in (a Nepali week starts on
    Sunday, and a week routinely straddles two months); the month view
    is the month's own cells.
  */
  const selectedDay = useMemo(
    () => days.find((day) => bsKey(day) === selected) ?? null,
    [days, selected]
  );

  const week = useMemo(
    () => (selectedDay ? weekOf(days, selectedDay) : []),
    [days, selectedDay]
  );

  /*
    READING A MONTH
    ---------------
    The month grid and the day's detail are two different reads: the
    grid is cheap and the detail is one request. They are fetched
    together only when a day is opened, and the grid is fetched only
    when the reader moves to another month.
  */
  const readMonth = useCallback(async (year: number, month: number) => {
    setBusy(true);
    setProblem(null);
    try {
      const response = await fetch(
        `/api/calendar?year=${year}&month=${month}&t=${Date.now()}`,
        { cache: "no-store" }
      );
      if (!response.ok) {
        setProblem("यो महिनाको पात्रो पढ्न मिलेन।");
        return;
      }
      const payload = (await response.json()) as {
        months?: CalendarMonth[];
        error?: string;
      };
      if (!payload.months || payload.months.length === 0) {
        setProblem(
          payload.error ?? "यो महिनाको पात्रो पढ्न मिलेन।"
        );
        return;
      }
      setRead(payload.months);
    } catch {
      setProblem("यो महिनाको पात्रो पढ्न मिलेन।");
    } finally {
      setBusy(false);
    }
  }, []);

  /*
    READING A YEAR
    --------------
    A year is four reads of the source's calendar page — the first
    month, the fourth, the seventh and the tenth — and the twelve
    months between them. It is only read when the reader asks for the
    year view, and it is cached for an hour on the server like
    everything else.
  */
  const readYear = useCallback(async (year: number) => {
    setBusy(true);
    setProblem(null);
    try {
      const response = await fetch(
        `/api/calendar?year=${year}&t=${Date.now()}`,
        { cache: "no-store" }
      );
      if (!response.ok) {
        setProblem("यो वर्षको पात्रो पढ्न मिलेन।");
        return;
      }
      const payload = (await response.json()) as {
        months?: CalendarMonth[];
        error?: string;
      };
      if (!payload.months || payload.months.length === 0) {
        setProblem(payload.error ?? "यो वर्षको पात्रो पढ्न मिलेन।");
        return;
      }
      setYearMonths(payload.months);
    } catch {
      setProblem("यो वर्षको पात्रो पढ्न मिलेन।");
    } finally {
      setBusy(false);
    }
  }, []);

  /*
    OPENING A DAY
    -------------
    The grid's cell knows the tithi and the events, but the panchang
    — the yoga, the karana, the nakshatra, the sunrise and the
    sunset — is on the day's own page, so it is read when the day is
    opened.

    The panel opens IMMEDIATELY, with what the grid already knows, and
    fills in when the read answers. A reader who clicks a date gets a
    panel at once, not a spinner.
  */
  const openDay = useCallback(
    async (day: CalendarDay, trigger: HTMLElement | null) => {
      triggerRef.current = trigger;
      setSelected(bsKey(day));
      setDetail(
        today && bsKey(today) === bsKey(day)
          ? today
          : ({
              ...day,
              panchanga: {
                tithi: day.tithiShort,
                yoga: null,
                karana: null,
                nakshatra: null,
              },
              sunrise: null,
              sunset: null,
              moonPhase: { emoji: null, nameNe: null, nameEn: null },
              nepalSambat: null,
              weekdayNe: null,
              monthNe: null,
              yearBsNe: null,
            } satisfies CalendarDayDetail)
      );
      setOpen(true);
      setProblem(null);

      /*
        THE ADDRESS FOLLOWS THE DAY.

        The page is addressed by the day it shows (`?date=`), so a
        reader who picks a date can send it to somebody and they will
        see the same day. The Gregorian date of the day the calendar
        holds is used, because that is what the page's own parameter
        is written in.
      */
      if (day.dateAd) {
        const query = new URLSearchParams();
        if (period && period !== "daily") query.set("period", period);
        query.set("date", day.dateAd);
        router.replace(`/category/rashifal?${query.toString()}`);
      }

      setBusy(true);
      try {
        const response = await fetch(
          `/api/calendar?date=${bsKey(day)}&t=${Date.now()}`,
          { cache: "no-store" }
        );
        if (!response.ok) return;
        const payload = (await response.json()) as {
          day?: CalendarDayDetail | null;
        };
        if (payload.day) setDetail(payload.day);
      } catch {
        // The panel is open with what the grid knew. That is enough.
      } finally {
        setBusy(false);
      }
    },
    [today, router, period]
  );

  /*
    A DAY OPENED FROM THE YEAR GRID
    -------------------------------
    The year grid is a different component, and the day it opens has to
    open the same panel the month grid opens. The grid announces the
    day and this listens for it — one event, one panel, one set of
    rules.
  */
  useEffect(() => {
    const onOpen = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | { day: CalendarDay; trigger: HTMLElement | null }
        | undefined;
      if (!detail?.day) return;
      void openDay(detail.day, detail.trigger ?? null);
    };
    window.addEventListener("nepali-calendar:open-day", onOpen);
    return () =>
      window.removeEventListener("nepali-calendar:open-day", onOpen);
  }, [openDay]);

  /*
    THE DAY TURNS
    -------------
    The grid is read for a day, and a tab left open across midnight
    Kathmandu time would show yesterday's "today" until the reader
    reloaded. The check is once a minute — a minute is the finest
    resolution that matters, and it costs nothing.
  */
  const dayAtStart = useRef(kathmanduDay());
  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = kathmanduDay();
      if (now === dayAtStart.current) return;
      dayAtStart.current = now;
      // The day turned: re-read the month so the grid, the "today"
      // mark and the selected day all move with it.
      void readMonth(cursor.year, cursor.month);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [cursor.year, cursor.month, readMonth]);

  /*
    THE PANEL IS A DIALOG
    ---------------------
    The same contract the story dialog on the reels page keeps, and
    for the same reasons: it is named, it is modal, the focus goes
    into it, the focus cannot leave it, Escape closes it, the page
    behind it does not scroll, and the focus goes back to the cell
    that opened it.

    The rules themselves live in lib/story-dialog.ts, where they can
    be tested without a browser.
  */
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isEscapeKey(event.key)) return;
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onFocusIn = (event: FocusEvent) => {
      const panel = panelRef.current;
      if (!panel) return;
      const target = event.target as Node | null;
      if (target && panel.contains(target)) return;
      const items = focusableWithin(panel);
      (items[0] ?? panel).focus({ preventScroll: true });
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    /*
      THE BACKGROUND IS NOT HIDDEN, AND THAT IS DELIBERATE.

      The reels page's story dialog sets `aria-hidden` on the page
      behind it, because the panel is a sibling of it. This panel is a
      CHILD of the calendar, so hiding the calendar would hide the
      panel with it. `aria-modal="true"` already tells the assistive
      technology that the rest of the page is unavailable while the
      panel is up, and the focus is trapped below, so the reader
      cannot get out of the panel either way.
    */
    focusThenHide(null, () => {
      panelRef.current?.focus({ preventScroll: true });
    });
    const unlock = lockBodyScroll(document.body);
    return () => {
      unlock();
      showThenRestore(null, () => {
        const target = triggerRef.current;
        if (target && target.isConnected) {
          target.focus({ preventScroll: true });
          return;
        }
        // The cell that opened the panel is gone (the month moved, or
        // the grid re-read). The focus still has to land somewhere
        // inside the calendar, or the reader's next Tab starts from
        // the top of the document.
        focusableWithin(
          document.getElementById("nepali-calendar")
        )[0]?.focus({ preventScroll: true });
      });
    };
  }, [open]);

  const handlePanelKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>
  ) => {
    if (event.key !== "Tab") return;
    const items = focusableWithin(panelRef.current);
    if (items.length === 0) {
      event.preventDefault();
      panelRef.current?.focus({ preventScroll: true });
      return;
    }
    const current = items.indexOf(document.activeElement as HTMLElement);
    const next = nextFocusIndex(current, items.length, event.shiftKey ? -1 : 1);
    event.preventDefault();
    items[next]?.focus({ preventScroll: true });
  };

  const goMonth = (year: number, month: number) => {
    setCursor({ year, month });
    void readMonth(year, month);
  };

  const month = visible;

  return (
    <section
      id="nepali-calendar"
      aria-labelledby="nepali-calendar-heading"
      className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900"
    >
      {/* ================= HEAD ================= */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 sm:px-5 dark:border-gray-700">
        <div className="min-w-0">
          <h2
            id="nepali-calendar-heading"
            className="font-headline text-lg font-bold text-gray-900 dark:text-gray-100"
          >
            नेपाली पात्रो
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {month
              ? `${month.monthNameNe} ${month.yearBsNe} · ${month.monthNameEn} ${month.yearBs}`
              : ""}
          </p>
        </div>

        {/*
          THE THREE READINGS.

          दिन, हप्ता, महिना — the day, the week, the month. The
          reader's question decides which one they want, and the
          switch is a real control rather than a link, because it
          changes what is on the page and not where the page is.
        */}
        <div
          role="group"
          aria-label="पात्रोको अवधि"
          className="flex flex-wrap gap-1 rounded-full bg-gray-100 p-1 dark:bg-gray-800"
        >
          {CALENDAR_VIEWS.map((entry) => {
            const active = entry.key === view;
            return (
              <button
                key={entry.key}
                type="button"
                onClick={() => {
                  setView(entry.key);
                  // The year is four reads of the publisher, so it is
                  // only made when the reader asks for it.
                  if (
                    entry.key === "year" &&
                    yearMonths.length === 0 &&
                    today
                  ) {
                    void readYear(today.yearBs);
                  }
                }}
                aria-pressed={active}
                className={
                  active
                    ? "min-h-[36px] rounded-full bg-blue-600 px-3 text-sm font-semibold text-white shadow-sm"
                    : "min-h-[36px] rounded-full px-3 text-sm font-semibold text-gray-600 transition hover:text-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:text-gray-300 dark:hover:text-blue-300"
                }
              >
                {entry.label}
              </button>
            );
          })}
        </div>
      </div>

      {problem ? (
        <p className="px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
          {problem}
        </p>
      ) : null}

      {/* ================= THE GRID ================= */}
      {view === "year" ? (
        <YearGrid
          months={yearMonths}
          todayKey={today ? bsKey(today) : null}
          selectedKey={selected}
          busy={busy}
          onOpenMonth={(year, month) => {
            setView("month");
            setCursor({ year, month });
            void readMonth(year, month);
          }}
        />
      ) : view === "month" ? (
        <MonthGrid
          month={month}
          todayKey={today ? bsKey(today) : null}
          selectedKey={selected}
          busy={busy}
          onOpenDay={openDay}
          onPrev={() =>
            month && goMonth(month.prev.year, month.prev.month)
          }
          onNext={() =>
            month && goMonth(month.next.year, month.next.month)
          }
          onToday={
            today
              ? () => {
                  const home = monthOf(read, {
                    yearBs: today.yearBs,
                    monthBs: today.monthBs,
                  });
                  if (!home) return;
                  setCursor({ year: home.yearBs, month: home.monthBs });
                  setSelected(bsKey(today));
                  setDetail(today);
                  void readMonth(home.yearBs, home.monthBs);
                }
              : null
          }
        />
      ) : view === "week" ? (
        <WeekStrip
          days={week}
          todayKey={today ? bsKey(today) : null}
          selectedKey={selected}
          onOpenDay={openDay}
        />
      ) : (
        <DayPanel
          day={selectedDay}
          detail={detail}
          busy={busy}
          onOpenDay={openDay}
        />
      )}

      {/*
        THE ATTRIBUTION IS NOT HERE.

        The page carries ONE attribution line, under the whole page,
        and this widget used to carry a second one of its own — so the
        reader saw "Source: hamropatro.com/date/2083-7-7" twice, once
        inside the calendar and once under it. One line, at the foot
        of the page, small enough not to be read, is an attribution.
        Two is a banner.
      */}

      {/* ================= THE PANEL ================= */}
      {open && detail ? (
        <div
          className="fixed inset-0 z-[70] flex items-end justify-center overflow-y-auto bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-6"
          onClick={(event) => {
            if (event.target !== event.currentTarget) return;
            setOpen(false);
          }}
        >
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            onKeyDown={handlePanelKeyDown}
            className="relative flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-white text-gray-900 shadow-2xl focus:outline-none sm:rounded-2xl dark:bg-gray-900 dark:text-gray-100"
          >
            <div className="flex items-start justify-between gap-4 border-b border-gray-200 p-5 dark:border-gray-700">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">
                  {detail.weekdayNe ?? ""}
                  {detail.dayOfWeek !== undefined
                    ? ` · ${WEEKDAYS_EN[detail.dayOfWeek % 7]}`
                    : ""}
                </p>
                <h3
                  id={titleId}
                  className="mt-1 font-headline text-xl font-bold leading-snug"
                >
                  {detail.monthNe ?? ""} {detail.dayBsNp},{" "}
                  {detail.yearBsNe ?? ""}
                </h3>
                {englishDate(detail.dateAd) ? (
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {englishDate(detail.dateAd)}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="बन्द गर्नुहोस्"
                className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:hover:bg-gray-800 dark:text-gray-200"
              >
                <span aria-hidden="true" className="block text-xl leading-none">
                  ×
                </span>
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {busy ? (
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  पञ्चाङ लोड हुँदैछ…
                </p>
              ) : null}

              <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                <PanchangRow label="तिथि" value={detail.panchanga.tithi} />
                <PanchangRow label="योग" value={detail.panchanga.yoga} />
                <PanchangRow label="करण" value={detail.panchanga.karana} />
                <PanchangRow
                  label="नक्षत्र"
                  value={detail.panchanga.nakshatra}
                />
                <PanchangRow
                  label="सूर्योदय"
                  value={detail.sunrise}
                />
                <PanchangRow
                  label="सूर्यास्त"
                  value={detail.sunset}
                />
                <PanchangRow
                  label="चन्द्र अवस्था"
                  value={
                    detail.moonPhase.emoji && detail.moonPhase.nameNe
                      ? `${detail.moonPhase.emoji} ${detail.moonPhase.nameNe}`
                      : detail.moonPhase.nameNe
                  }
                />
                <PanchangRow
                  label="नेपाल संवत्"
                  value={detail.nepalSambat}
                />
              </dl>

              {detail.events.length > 0 ? (
                <div className="mt-5">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    यो दिन
                  </h4>
                  <ul className="mt-2 space-y-1.5">
                    {detail.events.map((event) => (
                      <li
                        key={event.title}
                        className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300"
                      >
                        <span
                          aria-hidden="true"
                          className={
                            event.isHoliday
                              ? "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"
                              : "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500"
                          }
                        />
                        <span>
                          {event.title}
                          {event.titleEn ? (
                            <span className="block text-[11px] text-gray-500 dark:text-gray-400">
                              {event.titleEn}
                            </span>
                          ) : null}
                          {event.isHoliday ? (
                            <span className="ml-2 rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-semibold text-red-700 dark:bg-red-950/50 dark:text-red-300">
                              बिदा
                            </span>
                          ) : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <a
                href={sourceDayUrl(detail)}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="mt-6 inline-flex min-h-[48px] items-center rounded-lg border-2 border-blue-600 bg-blue-50 px-4 text-sm font-semibold text-blue-800 transition hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-blue-500 dark:bg-blue-950/50 dark:text-blue-200 dark:hover:bg-blue-900/50"
              >
                पूरा पात्रो हेर्नुहोस् ↗
              </a>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/**
 * One row of the panchang.
 *
 * A term with no value is not shown at all: a row that says "योग:" and
 * nothing after it is worse than no row, because it tells the reader
 * the site knows the answer and has lost it.
 */
function PanchangRow({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm font-medium text-gray-900 dark:text-gray-100">
        {value}
      </dd>
    </div>
  );
}

/**
 * The month, as a grid of days.
 *
 * The cells are the source's own — it knows where a month starts and
 * how many cells it needs — so the grid is exactly the one the
 * publisher draws, and the site does not compute a calendar it would
 * have to get right on its own.
 */
function MonthGrid({
  month,
  todayKey,
  selectedKey,
  busy,
  onOpenDay,
  onPrev,
  onNext,
  onToday,
}: {
  month: CalendarMonth | null;
  todayKey: string | null;
  selectedKey: string | null;
  busy: boolean;
  onOpenDay: (day: CalendarDay, trigger: HTMLElement | null) => void;
  onPrev: () => void;
  onNext: () => void;
  onToday: (() => void) | null;
}) {
  if (!month) {
    return (
      <p className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        पात्रो पढ्न मिलेन।
      </p>
    );
  }

  return (
    <div className="p-3 sm:p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onPrev}
          aria-label="अघिल्लो महिना"
          className="flex h-10 w-10 items-center justify-center rounded-lg border border-gray-300 text-gray-600 transition hover:border-blue-600 hover:text-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:border-gray-700 dark:text-gray-300 dark:hover:border-blue-400"
        >
          <span aria-hidden="true">‹</span>
        </button>
        <p className="text-center font-headline text-base font-bold text-gray-900 dark:text-gray-100">
          {month.monthNameNe} {month.yearBsNe}
          {month.monthNameEn || gregorianRange(month) ? (
            <span className="ml-2 text-xs font-normal text-gray-500 dark:text-gray-400">
              {month.monthNameEn}
              {month.monthNameEn && gregorianRange(month) ? " · " : ""}
              {gregorianRange(month)}
            </span>
          ) : null}
        </p>
        <button
          type="button"
          onClick={onNext}
          aria-label="अर्को महिना"
          className="flex h-10 w-10 items-center justify-center rounded-lg border border-gray-300 text-gray-600 transition hover:border-blue-600 hover:text-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:border-gray-700 dark:text-gray-300 dark:hover:border-blue-400"
        >
          <span aria-hidden="true">›</span>
        </button>
      </div>

      {/*
        TODAY.

        A reader who has walked three months forward and then wants
        today does not want to walk three months back, and a calendar
        that can only be moved one month at a time is a calendar that
        loses the reader.
      */}
      {onToday ? (
        <div className="mb-3 flex justify-center">
          <button
            type="button"
            onClick={onToday}
            className="inline-flex min-h-[36px] items-center rounded-full border border-blue-600 bg-blue-600 px-4 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-blue-500 dark:bg-blue-600 dark:hover:bg-blue-500"
          >
            आज
          </button>
        </div>
      ) : null}

      <div className="grid grid-cols-7 gap-1">
        {WEEKDAY_HEADERS.map((name) => (
          <div
            key={name}
            className="pb-1 text-center text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400"
          >
            {name}
          </div>
        ))}
      </div>

      <div
        className={`grid grid-cols-7 gap-1 transition-opacity ${
          busy ? "opacity-60" : ""
        }`}
      >
        {month.days.map((day) => {
          const key = bsKey(day);
          const isToday = key === todayKey;
          const isSelected = key === selectedKey;
          return (
            <button
              key={key}
              type="button"
              onClick={(event) => onOpenDay(day, event.currentTarget)}
              aria-label={cellLabel(day, month)}
              aria-current={isToday ? "date" : undefined}
              className={[
                "relative flex min-h-[88px] flex-col items-center rounded-lg border px-1.5 pb-1.5 pt-1 text-center transition",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1",
                isSelected
                  ? "border-blue-600 bg-blue-600 text-white"
                  : isToday
                    ? "border-blue-500 bg-blue-50 text-blue-900 dark:border-blue-500 dark:bg-blue-950/50 dark:text-blue-100"
                    : "border-gray-200 bg-white text-gray-800 hover:border-blue-400 hover:bg-blue-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700",
                day.inMonth ? "" : "opacity-50",
              ].join(" ")}
            >
              {/*
                THE GREGORIAN DAY, IN THE CORNER.

                The Bikram Sambat day is the one a Nepali reader reads
                first, and it is the one that is right for them. But
                the date they have to write on a form, or look up in a
                Western diary, is the Gregorian one - so it sits in
                the corner of the cell, in the same place the source
                puts it, and the cell reads in both calendars at once.
              */}
              {englishDay(day.dateAd) ? (
                <span
                  className={
                    isSelected
                      ? "absolute right-1.5 top-1 text-[10px] font-medium leading-none text-blue-100"
                      : "absolute right-1.5 top-1 text-[10px] font-medium leading-none text-gray-400 dark:text-gray-500"
                  }
                >
                  {englishDay(day.dateAd)}
                </span>
              ) : null}

              {/*
                WHAT HAPPENS ON THIS DAY.

                A festival, a public holiday, a fast - the things a
                Nepali reader is looking at the calendar FOR. They are
                the busiest thing in the cell and they are the reason
                the cell is as tall as it is.
              */}
              {day.events.length > 0 ? (
                <span
                  className={
                    isSelected
                      ? "mt-2 line-clamp-2 w-full text-[10px] font-medium leading-tight text-blue-100"
                      : "mt-2 line-clamp-2 w-full text-[10px] font-medium leading-tight text-blue-700 dark:text-blue-300"
                  }
                >
                  {day.events.map((event) => event.title).join(" / ")}
                </span>
              ) : null}

              <span
                className={
                  day.events.length > 0
                    ? "mt-1 font-headline text-xl font-bold leading-none"
                    : "mt-4 font-headline text-xl font-bold leading-none"
                }
              >
                {day.dayBsNp}
              </span>
              {day.tithiShort ? (
                <span
                  className={
                    isSelected
                      ? "mt-1 block max-w-full truncate text-[10px] leading-tight text-blue-100"
                      : "mt-1 block max-w-full truncate text-[10px] leading-tight text-gray-500 dark:text-gray-400"
                  }
                >
                  {day.tithiShort}
                </span>
              ) : null}
              {day.isHoliday ? (
                <span
                  aria-hidden="true"
                  className={
                    isSelected
                      ? "absolute left-1.5 top-1 h-1.5 w-1.5 rounded-full bg-red-300"
                      : "absolute left-1.5 top-1 h-1.5 w-1.5 rounded-full bg-red-500"
                  }
                />
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The year, as twelve months.
 *
 * A Nepali year is twelve months of 29 to 32 days, and a reader who
 * wants to know "when is Dashain this year" does not want to page
 * through twelve month views to find out. So the year is laid out the
 * way a year is: twelve small calendars, three across on a wide
 * screen and one on a phone, each with the days in place and today
 * marked.
 *
 * Clicking a month opens it.
 */
function YearGrid({
  months,
  todayKey,
  selectedKey,
  busy,
  onOpenMonth,
}: {
  months: CalendarMonth[];
  todayKey: string | null;
  selectedKey: string | null;
  busy: boolean;
  onOpenMonth: (year: number, month: number) => void;
}) {
  if (months.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        {busy ? "पात्रो लोड हुँदैछ…" : "यो वर्षको पात्रो पढ्न मिलेन।"}
      </p>
    );
  }

  return (
    <div
      className={`grid grid-cols-1 gap-4 p-3 sm:grid-cols-2 sm:p-4 lg:grid-cols-3 ${
        busy ? "opacity-60" : ""
      }`}
    >
      {months.map((month) => {
        const cells = month.days;
        return (
          <section
            key={`${month.yearBs}-${month.monthBs}`}
            className="rounded-xl border border-gray-200 p-3 dark:border-gray-700"
          >
            <button
              type="button"
              onClick={() => onOpenMonth(month.yearBs, month.monthBs)}
              className="block w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
            >
              <h3 className="font-headline text-sm font-bold text-gray-900 dark:text-gray-100">
                {month.monthNameNe}{" "}
                <span className="font-normal text-gray-500 dark:text-gray-400">
                  {month.monthNameEn}
                </span>
              </h3>
            </button>

            <div className="mt-2 grid grid-cols-7 gap-px text-center text-[9px] leading-none text-gray-400 dark:text-gray-500">
              {["आ", "सो", "मं", "बु", "बि", "शु", "श"].map((name) => (
                <span key={name}>{name}</span>
              ))}
            </div>

            <div className="mt-1 grid grid-cols-7 gap-px text-center">
              {cells.map((day) => {
                const key = bsKey(day);
                const isToday = key === todayKey;
                const isSelected = key === selectedKey;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenDayOf(month, day, event.currentTarget);
                    }}
                    aria-label={cellLabel(day, month)}
                    aria-current={isToday ? "date" : undefined}
                    className={[
                      "flex h-5 items-center justify-center rounded text-[10px] leading-none",
                      isSelected
                        ? "bg-blue-600 font-semibold text-white"
                        : isToday
                          ? "bg-blue-50 font-bold text-blue-700 dark:bg-blue-950/50 dark:text-blue-200"
                          : "text-gray-700 dark:text-gray-300",
                      day.inMonth ? "" : "opacity-40",
                    ].join(" ")}
                  >
                    {day.dayBs}
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

/**
 * The week, as a row of days.
 */
function WeekStrip({
  days,
  todayKey,
  selectedKey,
  onOpenDay,
}: {
  days: CalendarDay[];
  todayKey: string | null;
  selectedKey: string | null;
  onOpenDay: (day: CalendarDay, trigger: HTMLElement | null) => void;
}) {
  if (days.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        यो हप्ताको पात्रो पढ्न मिलेन।
      </p>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-4 lg:grid-cols-7">
      {days.map((day) => {
        const key = bsKey(day);
        const isToday = key === todayKey;
        const isSelected = key === selectedKey;
        return (
          <button
            key={key}
            type="button"
            onClick={(event) => onOpenDay(day, event.currentTarget)}
            aria-current={isToday ? "date" : undefined}
            className={[
              "flex min-h-[92px] flex-col items-center justify-center rounded-xl border px-2 py-3 text-center transition",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1",
              isSelected
                ? "border-blue-600 bg-blue-600 text-white"
                : isToday
                  ? "border-blue-500 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/50"
                  : "border-gray-200 bg-white hover:border-blue-400 hover:bg-blue-50 dark:border-gray-700 dark:bg-gray-800 dark:hover:bg-gray-700",
            ].join(" ")}
          >
            <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              {WEEKDAY_HEADERS[day.dayOfWeek % 7]}
            </span>
            <span className="mt-1 font-headline text-2xl font-bold leading-none">
              {day.dayBsNp}
            </span>
            {englishDay(day.dateAd) ? (
              <span className="text-[11px] leading-none text-gray-500 dark:text-gray-400">
                {englishDate(day.dateAd)}
              </span>
            ) : null}
            {day.tithiShort ? (
              <span
                className={
                  isSelected
                    ? "mt-1.5 text-[11px] text-blue-100"
                    : "mt-1.5 text-[11px] text-gray-500 dark:text-gray-400"
                }
              >
                {day.tithiShort}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The day, on its own.
 */
function DayPanel({
  day,
  detail,
  busy,
  onOpenDay,
}: {
  day: CalendarDay | null;
  detail: CalendarDayDetail | null;
  busy: boolean;
  onOpenDay: (day: CalendarDay, trigger: HTMLElement | null) => void;
}) {
  if (!day) {
    return (
      <p className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        आजको पात्रो पढ्न मिलेन।
      </p>
    );
  }

  const key = bsKey(day);
  const shown = detail && bsKey(detail) === key ? detail : null;

  return (
    <div className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            {WEEKDAY_HEADERS[day.dayOfWeek % 7]}
            {` · ${WEEKDAYS_EN[day.dayOfWeek % 7]}`}
          </p>
          <p className="font-headline text-3xl font-bold leading-tight text-gray-900 dark:text-gray-100">
            {day.dayBsNp}
            <span className="ml-2 text-base font-semibold text-gray-500 dark:text-gray-400">
              {day.tithiShort}
            </span>
          </p>
          {englishDate(day.dateAd) ? (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {englishDate(day.dateAd)}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={(event) => onOpenDay(day, event.currentTarget)}
          className="inline-flex min-h-[44px] items-center rounded-lg border-2 border-blue-600 bg-blue-50 px-4 text-sm font-semibold text-blue-800 transition hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-blue-500 dark:bg-blue-950/50 dark:text-blue-200 dark:hover:bg-blue-900/50"
        >
          विवरण हेर्नुहोस्
        </button>
      </div>

      {shown ? (
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <PanchangRow label="तिथि" value={shown.panchanga.tithi} />
          <PanchangRow label="योग" value={shown.panchanga.yoga} />
          <PanchangRow label="करण" value={shown.panchanga.karana} />
          <PanchangRow label="नक्षत्र" value={shown.panchanga.nakshatra} />
          <PanchangRow label="सूर्योदय" value={shown.sunrise} />
          <PanchangRow label="सूर्यास्त" value={shown.sunset} />
          <PanchangRow
            label="चन्द्र अवस्था"
            value={
              shown.moonPhase.emoji && shown.moonPhase.nameNe
                ? `${shown.moonPhase.emoji} ${shown.moonPhase.nameNe}`
                : shown.moonPhase.nameNe
            }
          />
          <PanchangRow label="नेपाल संवत्" value={shown.nepalSambat} />
        </dl>
      ) : busy ? (
        <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">
          पञ्चाङ लोड हुँदैछ…
        </p>
      ) : null}

      {day.events.length > 0 ? (
        <ul className="mt-4 space-y-1.5">
          {day.events.map((event) => (
            <li
              key={event.title}
              className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300"
            >
              <span
                aria-hidden="true"
                className={
                  event.isHoliday
                    ? "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"
                    : "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500"
                }
              />
              <span>{event.title}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
