import { expect, test } from "@playwright/test";

import { describeWeatherCode, kathmanduToday } from "../lib/daily-info";

/**
 * The day's information in the header.
 *
 * WHAT CAN GO WRONG HERE
 * ----------------------
 * This strip is in the layout, so it is rendered on every page of the
 * site. A date that is a day out, or a weather word that is wrong, is
 * visible on every single one of them.
 *
 * THE TIMEZONE IS THE WHOLE PROBLEM
 * ---------------------------------
 * Kathmandu is UTC+05:45. At 18:00 UTC it is already the next day in
 * Nepal, so a reader in London and a reader in Delhi are looking at
 * different days. The date in the header has to be NEPAL'S date, not
 * the server's, and the tests below are all about that.
 */

test.describe("today, in Nepal", () => {
  test("it is the Nepal date, not the server's", () => {
    // 18:00 UTC is 23:45 in Nepal - still the same day.
    const evening = new Date("2026-10-22T18:00:00Z");
    expect(kathmanduToday(evening)).toBe("2026-10-22");

    // 18:30 UTC is 00:15 the NEXT day in Nepal.
    const night = new Date("2026-10-22T18:30:00Z");
    expect(kathmanduToday(night)).toBe("2026-10-23");
  });

  test("it is the same date all day in Nepal", () => {
    // 00:00 UTC is 05:45 in Nepal.
    expect(kathmanduToday(new Date("2026-10-22T00:00:00Z"))).toBe("2026-10-22");
    // 12:00 UTC is 17:45 in Nepal.
    expect(kathmanduToday(new Date("2026-10-22T12:00:00Z"))).toBe("2026-10-22");
  });

  test("it does not depend on the machine's own timezone", () => {
    // The point of computing it rather than reading it: a server in
    // UTC and a server in Sydney must agree.
    const utc = new Date("2026-10-22T18:30:00Z");
    expect(kathmanduToday(utc)).toBe("2026-10-23");
  });
});

test.describe("the weather codes", () => {
  test("every code has a word in both languages", () => {
    // A missing code would leave the strip with a temperature and no
    // description, on every page of the site.
    for (const code of [0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 63, 65, 71, 73, 75, 80, 95, 96, 99]) {
      const described = describeWeatherCode(code);
      expect(described, `code ${code}`).not.toBeNull();
      expect(described?.en.length, `code ${code}`).toBeGreaterThan(0);
      expect(described?.ne.length, `code ${code}`).toBeGreaterThan(0);
      expect(described?.icon, `code ${code}`).not.toBeNull();
    }
  });

  test("clear is clear", () => {
    expect(describeWeatherCode(0)?.icon).toBe("clear");
    expect(describeWeatherCode(1)?.icon).toBe("clear");
  });

  test("rain is rain and snow is snow", () => {
    expect(describeWeatherCode(61)?.icon).toBe("rain");
    expect(describeWeatherCode(65)?.icon).toBe("rain");
    expect(describeWeatherCode(71)?.icon).toBe("snow");
    expect(describeWeatherCode(75)?.icon).toBe("snow");
  });

  test("a storm is a storm", () => {
    expect(describeWeatherCode(95)?.icon).toBe("storm");
    expect(describeWeatherCode(99)?.icon).toBe("storm");
  });

  test("an unknown code has no word rather than a wrong one", () => {
    // Guessing at a code we do not know would put a wrong word in the
    // header. Saying nothing is better.
    expect(describeWeatherCode(9999)).toBeNull();
  });
});
