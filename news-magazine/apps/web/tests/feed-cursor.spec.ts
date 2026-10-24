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
 * The reader asked, repeatedly and in the same words, for the newest
 * FETCHED stories to be at the top:
 *
 *     "the new articles must be shown on the top based on the time
 *      immediatley fetch show them on ui and unitll new come new show
 *      and move old abit down and same new always on top"
 *
 * The measurement says why it matters. A pass fetches about 250 rows,
 * and most of them are the archive catching up - so of the newest 500
 * stories the site holds, only 15 had been fetched in the last hour.
 * Ordered by publication time, the top of the page barely moved: the
 * reader reloaded and found one or two new cards and nothing else.
 *
 * So the order is by ARRIVAL - when we fetched the story - and the
 * cursor addresses the same pair of columns, because a cursor on any
 * other column puts the page boundary in the wrong place and the
 * reader silently loses stories.
 *
 * The NEW BADGE is a different rule and is not here: it is about
 * publication time (see isFreshlyPublished in
 * components/LiveArticleFeed.tsx), and the reader asked for that
 * separately.
 */

test.describe("the feed's order", () => {
  test("the window is the newest by ARRIVAL, not by publication", () => {
    expect(FEED_ORDER.map((entry) => entry.column)).toEqual([
      "ingested_at",
      "id",
    ]);
    expect(FEED_ORDER.every((entry) => entry.ascending === false)).toBe(true);
  });

  test("the most recently fetched comes first", () => {
    const fetchedNow = {
      id: "a",
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T09:00:00.000Z",
      source_count: 1,
    };
    const fetchedEarlier = {
      id: "b",
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(fetchedNow, fetchedEarlier)).toBeLessThan(0);
    expect(byArrival(fetchedEarlier, fetchedNow)).toBeGreaterThan(0);
  });

  test("a story published in June, fetched just now, IS the newest", () => {
    // The reverse of the old rule, and the whole point. The pipeline
    // fetches the archive as well as the news, and a story the
    // publisher sat on for months is new TO THIS SITE the moment it
    // arrives. Ordering by publication time buried it in the middle
    // of the page, and the reader saw a page that did not move.
    const oldButJustFetched = {
      id: "a",
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-06-17T14:29:00.000Z",
      source_count: 1,
    };
    const newStory = {
      id: "b",
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T09:30:00.000Z",
      source_count: 1,
    };

    expect(byArrival(oldButJustFetched, newStory)).toBeLessThan(0);
  });

  test("within one arrival, the id breaks the tie, and it is stable", () => {
    // Two rows fetched in the same instant. Without a second sort key
    // the server and the browser could disagree about which is first,
    // and the page would visibly reshuffle on every poll.
    const first = {
      id: "00000000-0000-5000-8000-000000000001",
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };
    const second = {
      id: "00000000-0000-5000-8000-000000000002",
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(first, second)).toBeGreaterThan(0);
    expect(byArrival(second, first)).toBeLessThan(0);
  });

  test("a row with no arrival time sorts as if it were from 1970", () => {
    const undated: {
      id: string;
      ingested_at: string | null;
      published_at: string | null;
      source_count: number | null;
    } = {
      id: "a",
      ingested_at: null,
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };
    const dated: typeof undated = {
      id: "b",
      ingested_at: "2026-10-22T08:00:00.000Z",
      published_at: "2026-10-22T10:00:00.000Z",
      source_count: 1,
    };

    expect(byArrival(dated, undated)).toBeLessThan(0);
  });
});

test.describe("the keyset cursor", () => {
  const cursor = {
    ingested_at: "2026-10-22T10:00:00.123456+00:00",
    id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
  };

  test("it addresses arrival time and the tiebreaker", () => {
    const filter = feedCursorFilter(cursor);

    expect(filter).not.toBeNull();
    expect(filter).toContain(`ingested_at.lt.${cursor.ingested_at}`);
    expect(filter).toContain(`ingested_at.eq.${cursor.ingested_at}`);
    expect(filter).toContain(`id.lt.${cursor.id}`);
  });

  test("it addresses the same column the feed is ordered by", () => {
    // A cursor on any other column puts the page boundary in the
    // wrong place, and the reader loses stories with no error and no
    // empty page - just rows that were never shown.
    const filter = feedCursorFilter(cursor) ?? "";

    expect(FEED_ORDER[0]?.column).toBe("ingested_at");
    expect(filter).toContain("ingested_at");
    expect(filter).not.toContain("published_at");
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
    expect(feedCursorFilter({ ingested_at: "", id: cursor.id })).toBeNull();
  });

  test("the cursor for a row is its arrival time and its id", () => {
    expect(
      cursorAfter({
        id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
        ingested_at: "2026-10-22T10:00:00.123456+00:00",
      })
    ).toEqual({
      ingested_at: "2026-10-22T10:00:00.123456+00:00",
      id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
    });
  });

  test("a row with no arrival time ends the archive", () => {
    // NULLs sort last, so nothing can follow one. The feed reports the
    // end of the archive rather than an infinite one.
    expect(
      cursorAfter({
        id: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
        ingested_at: null,
      })
    ).toBeNull();
  });
});
