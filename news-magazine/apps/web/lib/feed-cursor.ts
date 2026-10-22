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
 * NEWEST NEWS FIRST
 * ------------------
 * The feed is ordered by `published_at` — when the story was PUBLISHED
 * — with `ingested_at` (when we fetched it) as the tiebreaker.
 *
 * This used to order by arrival, on the argument that a publisher who
 * sat on a story for hours makes it new to this site when it finally
 * appears. The argument is true, and it is also the wrong thing to
 * show a reader: the top of the homepage was a story published in
 * JUNE, above today's news, because we had re-fetched it. A feed
 * whose first cards are four months old reads as broken, and the
 * reader is right.
 *
 * So the newest NEWS is on top, and within one publication moment the
 * most recently fetched copy wins — which is where the better picture
 * and the higher outlet count come from.
 */

/** Where the next page of the feed starts. */
export interface FeedCursor {
  /**
   * When the story was published.
   *
   * The cursor has to address the ordering the query actually runs.
   * The feed is ordered by publication time, with arrival as the
   * tiebreaker, so the cursor is on publication time and the id.
   */
  published_at: string;
  /** The tiebreaker, so a page boundary inside a run of identical
   *  timestamps cannot skip or repeat rows. */
  id: string;
}

/**
 * The columns the feed is ordered by, in order.
 *
 * `id` is the tiebreaker. Several outlets publish inside the same
 * second, so without a second sort key PostgreSQL is free to return
 * that run in any order it likes — which makes page boundaries inside
 * it skip and repeat rows.
 */
export const FEED_ORDER = [
  { column: "published_at", ascending: false },
  { column: "id", ascending: false },
] as const;

/**
 * The keyset filter for "the rows after this cursor".
 *
 * The comparison is the boolean identity of "strictly below the
 * cursor", written out:
 *
 *   published_at < ts  OR  (published_at = ts AND id < id)
 *
 * `id` is the tiebreaker because `published_at` is only
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
  if (!cursor?.published_at) return null;
  return `published_at.lt.${cursor.published_at},and(published_at.eq.${cursor.published_at},id.lt.${cursor.id})`;
}

/**
 * The cursor for whatever page of the feed ends at `article`.
 *
 * Null for an article with no arrival time, which is the end of the
 * archive: nothing can follow a row that sorts last.
 */
export function cursorAfter(article: {
  id: string;
  published_at: string | null;
}): FeedCursor | null {
  if (!article.published_at) return null;
  return { published_at: article.published_at, id: article.id };
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
  const left = a.published_at ? Date.parse(a.published_at) : 0;
  const right = b.published_at ? Date.parse(b.published_at) : 0;
  if (right !== left) return right - left;

  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  return (b.source_count ?? 0) - (a.source_count ?? 0);
}
