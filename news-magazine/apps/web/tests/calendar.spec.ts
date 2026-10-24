import { expect, test } from "@playwright/test";

import {
  allDays,
  bsKey,
  CALENDAR_VIEWS,
  formatBsDate,
  kathmanduDay,
  monthOf,
  normaliseDay,
  normaliseDetail,
  normaliseMonth,
  parseBsDate,
  parseBootstrap,
  weekOf,
} from "../lib/calendar-core";

/**
 * The Nepali calendar, read rather than computed.
 *
 * The Bikram Sambat calendar has months of 29 to 32 days, irregular
 * leap years, and months that occasionally repeat — so anything the
 * site computes for itself will eventually be wrong, and a calendar
 * that tells a reader the wrong day is worse than no calendar.
 *
 * These tests pin the READING of the source's data: the shapes, the
 * arithmetic over the days, and the week that straddles two months.
 * They are pure functions over plain data, so they run in
 * milliseconds and they cannot be broken by the source being slow.
 */

/** A day, in the shape the source gives it. */
function sourceDay(overrides: Record<string, unknown> = {}) {
  return {
    year_ad: 2026,
    month_ad: 10,
    day_ad: 24,
    year_bs: 2083,
    month_bs: 7,
    day_bs: 7,
    dayOfWeek: 6,
    inMonth: true,
    dayBsNp: "७",
    dayAdNp: "२४",
    isToday: true,
    isWeekendHoliday: true,
    isHoliday: false,
    daysRemaining: 0,
    tithiShort: "त्रयोदशी",
    patroEvent: {
      items: [{ titleNp: "रविसप्तमी व्रत", titleEn: "Ravi Vrata", isHoliday: true }],
      isHoliday: true,
    },
    userEvents: [],
    ...overrides,
  };
}

test.describe("the source's own data", () => {
  test("is read out of the JSON island", () => {
    const html = `<html><body><script id="hp-bootstrap" type="application/json">{"data":{"calendar":{"initialMonths":[]}}}</script></body></html>`;
    const payload = parseBootstrap(html);
    expect(payload).not.toBeNull();
    expect(
      (payload as { data: { calendar: { initialMonths: unknown[] } } }).data
        .calendar.initialMonths
    ).toEqual([]);
  });

  test("a page with no island is an ordinary state, not a crash", () => {
    expect(parseBootstrap("<html><body>nothing here</body></html>")).toBeNull();
    expect(parseBootstrap("<script id=\"hp-bootstrap\">not json</script>")).toBeNull();
    expect(parseBootstrap("")).toBeNull();
  });

  test("a day keeps the tithi, the events and both calendars", () => {
    const day = normaliseDay(sourceDay());
    expect(day).not.toBeNull();
    expect(day!.dayBs).toBe(7);
    expect(day!.tithiShort).toBe("त्रयोदशी");
    expect(day!.events).toEqual([
      {
        title: "रविसप्तमी व्रत",
        titleEn: "Ravi Vrata",
        isHoliday: true,
      },
    ]);
    expect(day!.dateAd).toBe("2026-10-24");
    expect(day!.isToday).toBe(true);
  });

  test("an object that is not a day is not a day", () => {
    expect(normaliseDay(null)).toBeNull();
    expect(normaliseDay({})).toBeNull();
    expect(normaliseDay({ year_bs: 2083 })).toBeNull();
  });

  test("a day with no events has none, rather than throwing", () => {
    const day = normaliseDay(sourceDay({ patroEvent: null }));
    expect(day!.events).toEqual([]);
  });

  test("a month keeps its cells, its neighbours and its name", () => {
    const month = normaliseMonth({
      yearBs: 2083,
      monthBs: 7,
      monthNameNp: "कार्तिक",
      monthNameEn: "Kartik",
      yearBsNp: "२०८३",
      daysInMonth: 30,
      days: [sourceDay(), sourceDay({ day_bs: 8, isToday: false })],
      prev: { year: 2083, month: 6 },
      next: { year: 2083, month: 8 },
    });

    expect(month).not.toBeNull();
    expect(month!.monthNameNe).toBe("कार्तिक");
    expect(month!.daysInMonth).toBe(30);
    expect(month!.days).toHaveLength(2);
    expect(month!.prev).toEqual({ year: 2083, month: 6 });
    expect(month!.next).toEqual({ year: 2083, month: 8 });
  });

  test("a day's detail carries the panchang by name", () => {
    const detail = normaliseDetail({
      ...sourceDay(),
      panchangaText: {
        tithi: "कार्तिक कृष्ण सप्तमी",
        yoga: "साध्य",
        karana: "बव",
        nakshatra: "पुष्य",
      },
      nepalSambat: { label: "नेपाल संवत् ११४६ कौलागा सप्तमी" },
      moonPhase: { emoji: "🌗", name: { np: "अन्तिम खण्ड", en: "Last Quarter" } },
      sunriseNp: "०६:१४",
      sunsetNp: "१७:२०",
      weekdayNp: "आइतवार",
      monthNp: "कार्तिक",
      yearBsNp: "२०८३",
    });

    expect(detail).not.toBeNull();
    expect(detail!.panchanga.tithi).toBe("कार्तिक कृष्ण सप्तमी");
    expect(detail!.panchanga.nakshatra).toBe("पुष्य");
    expect(detail!.sunrise).toBe("०६:१४");
    expect(detail!.sunset).toBe("१७:२०");
    expect(detail!.moonPhase.nameNe).toBe("अन्तिम खण्ड");
    expect(detail!.nepalSambat).toBe("नेपाल संवत् ११४६ कौलागा सप्तमी");
    expect(detail!.weekdayNe).toBe("आइतवार");
  });
});

test.describe("a day in the address", () => {
  test("is written as it is read", () => {
    expect(bsKey({ yearBs: 2083, monthBs: 7, dayBs: 7 })).toBe("2083-7-7");
    expect(formatBsDate(2083, 7, 7)).toBe("2083-7-7");
  });

  test("is read back the same way", () => {
    expect(parseBsDate("2083-7-7")).toEqual({ year: 2083, month: 7, day: 7 });
    expect(parseBsDate("2083-07-07")).toEqual({ year: 2083, month: 7, day: 7 });
  });

  test("is refused when it is not one", () => {
    // The address is the reader's to write, and a reader who writes a
    // wrong one gets the default view rather than an error.
    expect(parseBsDate("")).toBeNull();
    expect(parseBsDate(null)).toBeNull();
    expect(parseBsDate("last tuesday")).toBeNull();
    expect(parseBsDate("2083-13-1")).toBeNull();
    expect(parseBsDate("2083-7-99")).toBeNull();
  });
});

test.describe("the week", () => {
  // A Sunday-to-Saturday run of days, which is what a Nepali week is.
  const days = [1, 2, 3, 4, 5, 6, 7, 8].map((day) =>
    normaliseDay(
      sourceDay({ day_bs: day, dayOfWeek: (day - 1) % 7, isToday: false })
    )!
  );

  test("is the seven days around the day, starting on a Sunday", () => {
    const week = weekOf(days, { yearBs: 2083, monthBs: 7, dayBs: 7 });
    expect(week).toHaveLength(7);
    expect(week[0]!.dayOfWeek).toBe(0);
    expect(week[6]!.dayOfWeek).toBe(6);
    expect(week.map((day) => day.dayBs)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  test("straddles the end of a month", () => {
    // The last days of one month and the first of the next are in the
    // same week more often than not, and the week has to be able to
    // cross the boundary.
    const across = [
      normaliseDay(
        sourceDay({ month_bs: 6, day_bs: 28, dayOfWeek: 0, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 6, day_bs: 29, dayOfWeek: 1, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 6, day_bs: 30, dayOfWeek: 2, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 7, day_bs: 1, dayOfWeek: 3, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 7, day_bs: 2, dayOfWeek: 4, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 7, day_bs: 3, dayOfWeek: 5, isToday: false })
      )!,
      normaliseDay(
        sourceDay({ month_bs: 7, day_bs: 4, dayOfWeek: 6, isToday: false })
      )!,
    ];

    const week = weekOf(across, { yearBs: 2083, monthBs: 7, dayBs: 1 });
    expect(week).toHaveLength(7);
    expect(week.map((day) => `${day.monthBs}/${day.dayBs}`)).toEqual([
      "6/28",
      "6/29",
      "6/30",
      "7/1",
      "7/2",
      "7/3",
      "7/4",
    ]);
  });

  test("is empty for a day that is not there", () => {
    expect(weekOf(days, { yearBs: 2099, monthBs: 1, dayBs: 1 })).toEqual([]);
  });
});

test.describe("the months that were read", () => {
  const months = [
    normaliseMonth({
      yearBs: 2083,
      monthBs: 6,
      monthNameNp: "असोज",
      daysInMonth: 31,
      days: [
        normaliseDay(sourceDay({ month_bs: 6, day_bs: 31, isToday: false }))!,
      ],
      prev: { year: 2083, month: 5 },
      next: { year: 2083, month: 7 },
    })!,
    normaliseMonth({
      yearBs: 2083,
      monthBs: 7,
      monthNameNp: "कार्तिक",
      daysInMonth: 30,
      days: [
        normaliseDay(sourceDay({ day_bs: 1, isToday: false }))!,
        normaliseDay(sourceDay({ day_bs: 7 }))!,
      ],
      prev: { year: 2083, month: 6 },
      next: { year: 2083, month: 8 },
    })!,
  ];

  test("are found by the year and the month", () => {
    expect(monthOf(months, { yearBs: 2083, monthBs: 7 })?.monthNameNe).toBe(
      "कार्तिक"
    );
    expect(monthOf(months, { yearBs: 2083, monthBs: 5 })).toBeNull();
  });

  test("give every day once, in order, when they overlap", () => {
    // The three months the source returns overlap at their edges, and
    // the week is built from the whole set, so the days are keyed.
    const days = allDays(months);
    const keys = days.map((day) => bsKey(day));
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual([...keys].sort());
  });
});

test.describe("the three readings", () => {
  test("are the day, the week and the month", () => {
    expect(CALENDAR_VIEWS.map((view) => view.key)).toEqual([
      "day",
      "week",
      "month",
    ]);
    expect(CALENDAR_VIEWS.map((view) => view.label)).toEqual([
      "दिन",
      "हप्ता",
      "महिना",
    ]);
  });
});

test.describe("today, in Kathmandu", () => {
  test("is the same day for a reader in London and one in Delhi", () => {
    // 18:00 UTC is already the next day in Kathmandu, and still the
    // same day in London. The offset is written out because it is not
    // a round number of hours.
    const late = new Date("2026-10-23T18:00:00Z");
    expect(kathmanduDay(late)).toBe("2026-10-23");
    expect(kathmanduDay(new Date("2026-10-23T18:30:00Z"))).toBe("2026-10-24");
  });
});
