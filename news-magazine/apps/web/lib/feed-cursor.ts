/**
 * The feed's ordering, and the keyset cursor that pages through it.
 *
 * WHY THIS IS A SEPARATE FILE
 * ---------------------------
 * The order the feed is shown in and the cursor that pages through it
 * are ONE decision, not two. If they disagree, the page boundary falls
 * in the wrong place and the reader silently loses stories — no error,
 * no empty page, just rows that were never shown. Keeping them
 * together, and testable without a database, is the only way to know
 * they still agree.
 *
 * ARRIVAL, NOT PUBLICATION
 * -------------------------
 * The feed is ordered by `ingested_at` — when WE first saw the story.
 * A publisher can sit on a story for hours before it appears in their
 * feed; when it does appear, it is new to this site and belongs at the
 * top, even though its publication timestamp is older than stories
 * already on the page.
 *
 * The reverse matters just as much. A story published two minutes ago
 * that we fetched ten minutes ago has been on the homepage the whole
 * time; it should not leap back to the top every time the feed polls.
 * Sorting by arrival means a story moves up ONCE, when it arrives, and
 * then stays put.
 *
 * This used to order by `published_at` while the client sorted by
 * arrival. The two did not contradict each other on the page — the
 * client's sort won — but the WINDOW the server filled was the newest
 * by publication, so a story published hours ago and fetched a minute
 * ago was outside the window and never reached the page at all.
 */

/** Where the next page of the feed starts. */
export interface FeedCursor {
  /**
   * When we first saw the story.
   *
   * The whole point of the cursor: it addresses the arrival order the
   * feed is actually in, not a different one.
   */
  ingested_at: string;
  /** The tiebreaker, so a page boundary inside a run of identical
   *  arrival times cannot skip or repeat rows. */
  id: string;
}

/**
 * The columns the feed is ordered by, in order.
 *
 * `id` is the tiebreaker. Two stories ingested inside the same
 * transaction share an arrival time exactly, so without a second sort
 * key PostgreSQL is free to return that run in any order it likes.
 */
export const FEED_ORDER = [
  { column: "ingested_at", ascending: false },
  { column: "id", ascending: false },
] as const;

/**
 * The keyset filter for "the rows after this cursor".
 *
 * The comparison is the boolean identity of "strictly below the
 * cursor", written out:
 *
 *   ingested_at < ts  OR  (ingested_at = ts AND id < id)
 *
 * `id` is the tiebreaker because `ingested_at` is only
 * microsecond-resolution and several stories are routinely ingested in
 * the same instant; without it, a page boundary landing inside a run of
 * equal timestamps would skip or repeat those rows.
 *
 * The filter is returned WITHOUT its own outer parentheses.
 * postgrest-js wraps whatever `.or()` is given in a pair already, and a
 * second pair makes PostgREST reject the whole tree with "failed to
 * parse logic tree" — measured against the live server, which is how
 * this was found.
 *
 * Null for a cursor that addresses nothing, which is what an undated
 * row produces: NULLs sort last, so there is no row below one to
 * compare against, and the feed reports the end of the archive rather
 * than an infinite one.
 */
export function feedCursorFilter(cursor: FeedCursor | null | undefined): string | null {
  if (!cursor?.ingested_at) return null;
  return `ingested_at.lt.${cursor.ingested_at},and(ingested_at.eq.${cursor.ingested_at},id.lt.${cursor.id})`;
}

/**
 * The cursor for whatever page of the feed ends at `article`.
 *
 * Null for an article with no arrival time, which is the end of the
 * archive: nothing can follow a row that sorts last.
 */
export function cursorAfter(article: {
  id: string;
  ingested_at: string | null;
}): FeedCursor | null {
  if (!article.ingested_at) return null;
  return { ingested_at: article.ingested_at, id: article.id };
}

/**
 * The order the feed is shown in, for the client.
 *
 * The same rule as the query, expressed as a comparator: arrival
 * first, publication second, and the number of outlets carrying the
 * story last. It has to agree with the query's ordering, because the
 * two are the same decision seen from two sides.
 */
export function byArrival<
  T extends {
    ingested_at?: string | null;
    published_at?: string | null;
    source_count?: number | null;
  },
>(a: T, b: T): number {
  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  const left = a.published_at ? Date.parse(a.published_at) : 0;
  const right = b.published_at ? Date.parse(b.published_at) : 0;
  if (right !== left) return right - left;

  return (b.source_count ?? 0) - (a.source_count ?? 0);
}
