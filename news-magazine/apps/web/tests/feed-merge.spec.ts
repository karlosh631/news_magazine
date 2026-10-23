import { expect, test } from "@playwright/test";

import { byNewest, mergeArticles } from "../lib/feed-merge";

/**
 * A live feed that changes silently is a live feed that is broken.
 *
 * The reader asked for the one thing a live news feed is for: a story
 * that arrives now appears at the top, and the stories that were
 * there move down.
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

test.describe("a story that arrives", () => {
  test("goes on top and pushes the page down", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    const fresh = [row("new", "2026-10-23T09:30:00Z"), ...current];

    const merged = mergeArticles(current, fresh);

    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("keeps the stories already on screen where they were", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
    const fresh = [row("new", "2026-10-23T11:00:00Z"), ...current];

    const merged = mergeArticles(current, fresh);

    // "a" and "b" keep their order and their places below the arrival.
    expect(merged.map((r) => r.id)).toEqual(["new", "a", "b"]);
  });

  test("does not move anything when nothing is new", () => {
    const current = [row("a", "2026-10-23T10:00:00Z"), row("b", "2026-10-23T09:00:00Z")];
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

  test("a story published an hour ago still arrives on top", () => {
    // It is new to this site, and the site is what the reader is
    // looking at. Burying it in the middle of the page is what made
    // it look like nothing was happening.
    const current = [row("a", "2026-10-23T10:00:00Z")];
    const fresh = [row("old-but-new", "2026-10-23T09:00:00Z"), ...current];

    expect(mergeArticles(current, fresh).map((r) => r.id)).toEqual([
      "old-but-new",
      "a",
    ]);
  });
});

test.describe("the order of a fresh page", () => {
  test("is newest first, by publication time", () => {
    const rows = [
      row("older", "2026-10-23T08:00:00Z"),
      row("newer", "2026-10-23T10:00:00Z"),
    ];

    expect([...rows].sort(byNewest).map((r) => r.id)).toEqual(["newer", "older"]);
  });
});
