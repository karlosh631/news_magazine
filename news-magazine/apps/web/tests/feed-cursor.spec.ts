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
 * The reader asked, repeatedly, for the most recently FETCHED story to
 * be at the top of the homepage. The page already sorted by arrival —
 * `lib/feed-merge.ts` does it on the client — but the QUERY that
 * filled the window was ordered by publication time, so the two
 * disagreed:
 *
 *   the window was the newest stories by PUBLICATION,
 *   the page showed the newest of those by ARRIVAL.
 *
 * The newest-arrived story was only on the page if it also happened to
 * be among the newest published. A publisher who sat on a story for
 * five hours before filing it in their feed produced a story that was
 * new to this site and invisible on it, and no amount of sorting
 * client-side could bring it back — it was never in the window.
 *
 * The order and the cursor are ONE decision. The cursor has to
 * address the same ordering the query runs, or the page boundary
 * falls in the wrong place and stories are silently lost — no error,
 * no empty page, just rows the reader never sees.
 */

test.describe("the feed's order", () => {
  test("the window is the newest by arrival, not by publication", () => {
    expect(FEED_ORDER.map((entry) => entry.column)).toEqual([
      "ingested_at",
      "id",
    ]);
    expect(FEED_ORDER.every((entry) => entry.ascending === false)).toBe(true);
  });

  test("arrival outranks publication", () => {
    // Fetched a minute ago, published five hours ago. It is new to this
    // site, so it belongs on top.
    const justFetched = {
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T05:00:00.000Z",
      source_count: 1,
    };
    const justPublished = {
      ingested_at: "2026-10-22T09:00:00.000Z",
      published_at: "2026-10-22T09:59:00.000Z",
      source_count: 1,
    };

    expect(byNewest(justFetched, justPublished)).toBeLessThan(0);
    expect(byNewest(justPublished, justFetched)).toBeGreaterThan(0);
  });

  test("a story moves up once, when it arrives, and then stays put", () => {
    // Published two minutes ago but fetched ten minutes ago. It has
    // been on the page the whole time, so it must NOT leap back above
    // a story that arrived after it.
    const earlier = {
      ingested_at: "2026-10-22T09:50:00.000Z",
      published_at: "2026-10-22T09:58:00.000Z",
      source_count: 1,
    };
    const later = {
      ingested_at: "2026-10-22T09:59:00.000Z",
      published_at: "2026-10-22T09:00:00.000Z",
      source_count: 1,
    };

    expect(byNewest(later, earlier)).toBeLessThan(0);
  });

  test("a row with no arrival time falls back to publication time", () => {
    // An older database, or a resurrected row. It must not sort as if
    // it were from 1970.
    const undated = {
      ingested_at: null,
      published_at: "2026-10-22T09:00:00.000Z",
      source_count: 1,
    };
    const dated = {
      ingested_at: "2026-10-22T10:00:00.000Z",
      published_at: "2026-10-22T05:00:00.000Z",
      source_count: 1,
    };

    expect(byNewest(dated, undated)).toBeLessThan(0);
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

  test("it does not address publication time", () => {
    // A publication-time cursor addresses a different ordering than
    // the one the query runs. Applying it would skip rows silently.
    const filter = feedCursorFilter(cursor) ?? "";

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

/** The comparator, named the way the tests read. */
function byNewest(
  a: {
    ingested_at: string | null;
    published_at: string | null;
    source_count: number;
  },
  b: {
    ingested_at: string | null;
    published_at: string | null;
    source_count: number;
  }
): number {
  return byArrival(a, b);
}
