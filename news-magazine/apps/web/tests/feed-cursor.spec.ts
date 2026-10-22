import { expect, test } from "@playwright/test";

import {
  FEED_ORDER,
  byArrival,
  cursorAfter,
  feedCursorFilter,
} from "../lib/feed-cursor";

/**
 * The feed's ordering and its cursor.
 *
 * WHY THESE EXIST
 * ---------------
 * The reader asked, repeatedly, for the latest news to be at the top of
 * every page. It was not, and the reason was a rule that sounded
 * right and was not.
 *
 * The feed was ordered by ARRIVAL — when WE fetched the story — on the
 * argument that a publisher who sat on a story for hours makes it new
 * to this site when it finally appears. The argument is true. It is
 * also the wrong thing to show somebody who opened a news site: the
 * top of the homepage was a story published in JUNE, sitting above
 * today's news, because we had re-fetched it.
 *
 * The order is therefore: the newest NEWS first, by publication time,
 * and within one publication moment the most recently fetched copy
 * first. The cursor addresses the same pair of columns, because a
 * cursor on any other column puts the page boundary in the wrong
 * place and the reader silently loses stories.
 */

test.describe("the feed's order", () => {
  test("the window is the newest by publication, not by arrival", () => {
    expect(FEED_ORDER.map((entry) => entry.column)).toEqual([
      "published_at",
      "id",
    ]);
    expect(FEED_ORDER.every((entry) => entry.ascending === false)).toBe(true);
  });

  test("the newest news comes first", () => {
    // Published ten minutes ago beats published an hour ago, whatever
    // order we happened to fetch them in.
    const publishedNow = {
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };
    const publishedEarlier = {
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T09:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(publishedNow, publishedEarlier)).toBeLessThan(0);
    expect(byArrival(publishedEarlier, publishedNow)).toBeGreaterThan(0);
  });

  test("a story published in June does not outrank one published today", () => {
    // The regression in one assertion: this is what the top of the
    // homepage looked like, and it is what the reader was looking at.
    const oldButJustFetched = {
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-06-17T14:29:00.000Z",
      source_count: 1,
    };
    const newStory = {
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T09:30:00.000Z",
      source_count: 1,
    };

    expect(byArrival(newStory, oldButJustFetched)).toBeLessThan(0);
  });

  test("within one moment, the most recently fetched copy wins", () => {
    // Two outlets file the same story in the same second. The copy we
    // fetched last is the one with the better picture.
    const fetchedFirst = {
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };
    const fetchedLast = {
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(fetchedLast, fetchedFirst)).toBeLessThan(0);
  });

  test("a row with no publication time falls back to arrival", () => {
    // An older database, or a resurrected row. It must not sort as if
    // it were from 1970.
    const undated: {
      ingested_at: string | null;
      published_at: string | null;
      source_count: number | null;
    } = {
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: null,
      source_count: 1,
    };
    const dated: typeof undated = {
      ingested_at: "2026-10-22T08:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(dated, undated)).toBeLessThan(0);
  });
});

test.describe("the keyset cursor", () => {
  const cursor = {
    published_at: "2026-10-22T10:00:00.123456+00:00",
    id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
  };

  test("it addresses publication time and the tiebreaker", () => {
    const filter = feedCursorFilter(cursor);

    expect(filter).not.toBeNull();
    expect(filter).toContain(`published_at.lt.${cursor.published_at}`);
    expect(filter).toContain(`published_at.eq.${cursor.published_at}`);
    expect(filter).toContain(`id.lt.${cursor.id}`);
  });

  test("it addresses the same column the feed is ordered by", () => {
    // A cursor on any other column puts the page boundary in the
    // wrong place, and the reader loses stories with no error and no
    // empty page — just rows that were never shown.
    const filter = feedCursorFilter(cursor) ?? "";

    expect(FEED_ORDER[0]?.column).toBe("published_at");
    expect(filter).toContain("published_at");
    expect(filter).not.toContain("ingested_at");
  });

  test("it carries no wrapping pair of its own", () => {
    // postgrest-js wraps whatever `.or()` is given in a pair already,
    // and a second pair makes PostgREST reject the whole tree with
    // "failed to parse logic tree". This was measured against the live
    // server, which is how it was found.
    //
    // The rule is about a WRAPPING pair, not about the closing
    // parenthesis of the inner `and(...)`, which the filter needs.
    const filter = feedCursorFilter(cursor) ?? "";

    expect(filter.startsWith("(") && filter.endsWith(")")).toBe(false);

    // And the parentheses it does have are balanced.
    const opens = filter.split("(").length - 1;
    const closes = filter.split(")").length - 1;
    expect(opens).toBe(closes);
  });

  test("a cursor that addresses nothing yields no filter", () => {
    expect(feedCursorFilter(null)).toBeNull();
    expect(feedCursorFilter(undefined)).toBeNull();
    expect(feedCursorFilter({ published_at: "", id: cursor.id })).toBeNull();
  });

  test("the cursor for a row is its publication time and its id", () => {
    expect(
      cursorAfter({
        id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
        published_at: "2026-10-22T10:00:00.123456+00:00",
      })
    ).toEqual({
      published_at: "2026-10-22T10:00:00.123456+00:00",
      id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
    });
  });

  test("a row with no publication time ends the archive", () => {
    // NULLs sort last, so nothing can follow one. The feed reports the
    // end of the archive rather than an infinite one.
    expect(
      cursorAfter({
        id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
        published_at: null,
      })
    ).toBeNull();
  });
});
