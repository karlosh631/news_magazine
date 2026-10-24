import { expect, test } from "@playwright/test";

import { byNewest, mergeArticles } from "../lib/feed-merge";

/**
 * A live feed that changes silently is a live feed that is broken.
 *
 * The reader asked for the one thing a live news feed is for, in the
 * same words several times:
 *
 *     "the new articles must be shown on the top based on the time
 *      immediatley fetch show them on ui and unitll new come new show
 *      and move old abit down and same new always on top"
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

/**
 * A row, fetched at `fetched` and published at `published`.
 *
 * The two are different clocks on purpose: the feed is ordered by the
 * first, and the NEW badge is about the second.
 */
function row(
  id: string,
  published: string,
  fetched: string = published
): Row {
  return { id, published_at: published, ingested_at: fetched };
}

test.describe("a story that arrives", () => {
  test("goes on top and pushes the page down", () => {
    const current = [
      row("a", "2026-10-23T10:00:00Z"),
      row("b", "2026-10-23T09:00:00Z"),
    ];
    // Fetched a minute ago, so it is ahead of everything on the page.
    const fresh = [row("new", "2026-10-23T10:30:00Z"), ...current];

    const merged = mergeArticles(current, fresh);

    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("keeps the stories already on screen where they were", () => {
    const current = [
      row("a", "2026-10-23T10:00:00Z"),
      row("b", "2026-10-23T09:00:00Z"),
    ];
    const fresh = [row("new", "2026-10-23T11:00:00Z"), ...current];

    const merged = mergeArticles(current, fresh);

    // "a" and "b" keep their order and their places below the arrival.
    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("does not move anything when nothing is new", () => {
    const current = [
      row("a", "2026-10-23T10:00:00Z"),
      row("b", "2026-10-23T09:00:00Z"),
    ];
    const fresh = [row("b", "2026-10-23T09:00:00Z"), row("a", "2026-10-23T10:00:00Z")];

    const merged = mergeArticles(current, fresh);

    expect(merged.map((r) => r.id)).toEqual(["a", "b"]);
  });

  test("hands back the same array when the poll found nothing", () => {
    const current = [row("a", "2026-10-23T10:00:00Z")];

    // The same array instance: React skips the re-render entirely, and
    // the page does not move by a pixel.
    expect(mergeArticles(current, [])).toBe(current);
  });

  test("a story published an hour ago and fetched just now IS an arrival", () => {
    // The archive and the news arrive together. The feed is ordered by
    // ARRIVAL, so the story the publisher sat on for an hour is new to
    // this site and lands at the top - which is what the reader asked
    // for, and what the old publication-time rule refused to do.
    const current = [row("a", "2026-10-23T10:00:00Z", "2026-10-23T10:00:00Z")];
    const fresh = [
      row("old-but-new", "2026-10-23T09:00:00Z", "2026-10-23T10:30:00Z"),
      ...current,
    ];

    expect(mergeArticles(current, fresh).map((r) => r.id)).toEqual([
      "old-but-new",
      "a",
    ]);
  });

  test("the archive the poll brought back does not jump the queue", () => {
    // A row the page already holds, fetched earlier, is not an
    // arrival. It keeps its place.
    const current = [
      row("a", "2026-10-23T10:00:00Z", "2026-10-23T10:00:00Z"),
      row("b", "2026-10-23T09:00:00Z", "2026-10-23T09:00:00Z"),
    ];
    const fresh = [
      row("new", "2026-10-23T10:30:00Z", "2026-10-23T10:30:00Z"),
      row("archive-1", "2026-10-23T08:00:00Z", "2026-10-23T08:00:00Z"),
      row("archive-2", "2026-10-23T07:00:00Z", "2026-10-23T07:00:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh);

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
    // A fetch that brings fifteen stories brings fifteen stories. The
    // old rule - "an arrival must be newer than the newest story
    // already on the page", compared by PUBLICATION time - kept only
    // the one that was newer than the top of the page, so a fetch that
    // delivered a batch showed ONE new card and hid the other
    // fourteen below the fold.
    //
    // The rule is now ARRIVAL: every row of the batch was fetched
    // after the page's newest row, so every row of the batch is an
    // arrival, and the whole batch lands on top in the order it was
    // fetched.
    const current = [row("old-1", "2026-10-23T08:00:00Z", "2026-10-23T08:00:00Z")];
    const fresh = [
      row("batch-1", "2026-10-23T10:55:00Z", "2026-10-23T11:00:00Z"),
      row("batch-2", "2026-10-23T10:40:00Z", "2026-10-23T10:59:00Z"),
      row("batch-3", "2026-10-23T10:20:00Z", "2026-10-23T10:58:00Z"),
      row("batch-4", "2026-10-23T09:50:00Z", "2026-10-23T10:57:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh);

    expect(merged.map((r) => r.id)).toEqual([
      "batch-1",
      "batch-2",
      "batch-3",
      "batch-4",
      "old-1",
    ]);
  });

  test("the batch keeps the page's own rows below it", () => {
    const current = [
      row("old-1", "2026-10-23T08:00:00Z", "2026-10-23T08:00:00Z"),
      row("old-2", "2026-10-23T07:00:00Z", "2026-10-23T07:00:00Z"),
    ];
    const fresh = [
      row("batch-1", "2026-10-23T10:55:00Z", "2026-10-23T11:00:00Z"),
      row("batch-2", "2026-10-23T10:40:00Z", "2026-10-23T10:59:00Z"),
      ...current,
    ];

    const merged = mergeArticles(current, fresh);

    expect(merged.map((r) => r.id)).toEqual([
      "batch-1",
      "batch-2",
      "old-1",
      "old-2",
    ]);
  });
});

test.describe("the order of a fresh page", () => {
  test("is the newest FETCHED first", () => {
    const rows = [
      row("older", "2026-10-23T10:00:00Z", "2026-10-23T08:00:00Z"),
      row("newer", "2026-10-23T08:00:00Z", "2026-10-23T10:00:00Z"),
    ];

    expect([...rows].sort(byNewest).map((r) => r.id)).toEqual([
      "newer",
      "older",
    ]);
  });

  test("a story fetched just now but published an hour ago IS the newest", () => {
    // The rule the reader asked for, in one assertion.
    const rows = [
      row("old-but-new", "2026-10-23T09:00:00Z", "2026-10-23T10:30:00Z"),
      row("newest", "2026-10-23T10:00:00Z", "2026-10-23T10:10:00Z"),
    ];

    expect([...rows].sort(byNewest).map((r) => r.id)).toEqual([
      "old-but-new",
      "newest",
    ]);
  });
});
