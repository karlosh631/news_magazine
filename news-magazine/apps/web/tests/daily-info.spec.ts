import { expect, test } from "@playwright/test";

import { kathmanduToday } from "../lib/daily-info";

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
