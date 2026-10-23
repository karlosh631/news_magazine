import { expect, test } from "@playwright/test";

import { byNewest, mergeArticles } from "../lib/feed-merge";

/**
 * A live feed that changes silently is a live feed that is broken.
 *
 * The reader asked for the one thing a live news feed is for: a story
 * that arrives now appears at the top, and the stories that were there
 * move down.
 *
 * The rule has to be narrow. A poll that returns the same hundred
 * rows every ten seconds must not reshuffle the page under the
 * reader, and the header's own refresh re-renders the server tree
 * every fifteen seconds - so only the rows that are genuinely new may
 * move.
 */

type Row = {
  id: string;
  published_at: string | null;
  ingested_at?: string | null;
};

function row(id: string, published: string, ingested = published): Row {
  return { id, published_at: published, ingested_at: ingested };
}

/**
 * A fixed clock, so the arrival window does not depend on the hour of
 * the day the tests are run. Eleven o'clock on the twenty-third.
 */
const NOW = Date.parse("2026-10-23T11:00:00Z");

test.describe("a story that arrives", () => {
  test("goes on top and pushes the page down", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    // Published a minute ago, so it is ahead of everything on the page.
    const fresh = [row("new", "2026-10-23T10:30:00Z"), ...current];

    const merged = mergeArticles(current, fresh, NOW);

    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("keeps the stories already on screen where they were", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    const fresh = [row("new", "2026-10-23T11:00:00Z"), ...current];

    const merged = mergeArticles(current, fresh, NOW);

    // "a" and "b" keep their order and their places below the arrival.
    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("does not move anything when nothing is new", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    const fresh = [row("b", "2026-10-23T09:00:00Z"), row("a", "2026-10-23T10:00:00Z")];

    const merged = mergeArticles(current, fresh, NOW);

    expect(merged.map((r) => r.id)).toEqual(["a", "b"]);
  });

  test("hands back the same array when the poll found nothing", () => {
    const current = [row("a", "2026-10-23T10:00:00Z")];

    // The same array instance: React skips the re-render entirely, and
    // the page does not move by a pixel.
    expect(mergeArticles(current, [], NOW)).toBe(current);
  });

  test("a story published an hour ago takes its place, not the top", () => {
    // The page is the newest NEWS. A story the worker only just
    // reached, but which the publisher posted two hours ago, is an
    // old story, and the reader who opened a news site for the news
    // does not want it above the news.
    //
    // (Ordering the page by ARRIVAL was tried and was wrong: the
    // pipeline reads a couple of pages of every feed, so the archive is
    // fetched last, and the top of the page filled with nine o'clock
    // stories that had simply arrived a moment ago.)
    const current = [row("a", "2026-10-23T10:00:00Z")];
    const fresh = [
      row("old-but-new", "2026-10-23T09:00:00Z", "2026-10-23T10:30:00Z"),
      ...current,
    ];

    expect(mergeArticles(current, fresh, NOW).map((r) => r.id)).toEqual([
      "a",
      "old-but-new",
    ]);
  });

  test("the archive the poll brought back does not jump the queue", () => {
    // The poll asks for a hundred rows and the page carries two. The
    // other ninety-eight are the ARCHIVE, and treating them as
    // arrivals put ninety-eight older stories on top of the page
    // every ten seconds.
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    const fresh = [
      row("new", "2026-10-23T10:30:00Z"),
      row("archive-1", "2026-10-23T08:00:00Z"),
      row("archive-2", "2026-10-23T07:00:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh, NOW);

    // The arrival is on top; the archive is below the page's own rows.
    expect(merged[0]!.id).toBe("new");
    expect(merged.map((r) => r.id)).toContain("archive-1");
    // And the page's own rows keep their order.
    expect(merged.map((r) => r.id).indexOf("a")).toBeLessThan(
      merged.map((r) => r.id).indexOf("b")
    );
  });
});

test.describe("a whole batch that arrives at once", () => {
  test("every story in the batch lands at the top, not only the newest", () => {
    // THE READER'S COMPLAINT.
    //
    // A fetch that brings fifteen stories brings fifteen stories from
    // the last hour. The old rule - "an arrival must be newer than the
    // newest story already on the page" - kept only the one that was
    // newer than the top of the page, so a fetch that delivered a
    // batch showed ONE new card and hid the other fourteen below the
    // fold. The feed looked frozen: one card at the top, then the
    // stories that were already there.
    //
    // Now the whole batch lands at the top, newest first among
    // themselves, and the stories that were on screen move down.
    const current = [row("old-1", "2026-10-23T09:00:00Z")];
    const fresh = [
      row("batch-1", "2026-10-23T10:55:00Z"),
      row("batch-2", "2026-10-23T10:40:00Z"),
      row("batch-3", "2026-10-23T10:20:00Z"),
      row("batch-4", "2026-10-23T09:50:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh, NOW);

    expect(merged.map((r) => r.id)).toEqual([
      "batch-1",
      "batch-2",
      "batch-3",
      "batch-4",
      "old-1",
    ]);
  });

  test("a story from outside the window keeps its place in the order", () => {
    // Ninety minutes is the window. A story older than that is the
    // archive, whatever fetched it.
    const current = [row("a", "2026-10-23T10:00:00Z")];
    const fresh = [
      row("inside", "2026-10-23T09:45:00Z"),
      row("outside", "2026-10-23T09:00:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh, NOW);

    expect(merged[0]!.id).toBe("inside");
    // The one outside the window is ordered by publication time, below.
    expect(merged.map((r) => r.id)).toEqual(["inside", "a", "outside"]);
  });

  test("a story from the future is treated as news, not as an error", () => {
    // Some publishers' clocks run ahead. A story stamped five minutes
    // in the future is the newest thing there is.
    const current = [row("a", "2026-10-23T10:00:00Z")];
    const fresh = [row("ahead", "2026-10-23T11:05:00Z"), ...current];

    const merged = mergeArticles(current, fresh, NOW);

    expect(merged[0]!.id).toBe("ahead");
  });
});

test.describe("the order of a fresh page", () => {
  test("is the newest NEWS first, by publication time", () => {
    const rows = [
      row("older", "2026-10-23T08:00:00Z"),
      row("newer", "2026-10-23T10:00:00Z"),
    ];

    expect([...rows].sort(byNewest).map((r) => r.id)).toEqual(["newer", "older"]);
  });

  test("a story fetched just now but published an hour ago is not the newest", () => {
    // The pipeline reads a couple of pages of every feed, so it fetches
    // the archive; ordering by arrival put the archive at the top.
    const rows = [
      row("old-but-new", "2026-10-23T09:00:00Z", "2026-10-23T10:30:00Z"),
      row("newest", "2026-10-23T10:00:00Z", "2026-10-23T10:10:00Z"),
    ];

    expect([...rows].sort(byNewest).map((r) => r.id)).toEqual([
      "newest",
      "old-but-new",
    ]);
  });
});
