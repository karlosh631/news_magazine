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
 * ORDER OF ARRIVAL, NOT PUBLICATION TIME
 * ---------------------------------------
 * The feed is ordered by `ingested_at` — when WE fetched the story —
 * with `id` as the tiebreaker.
 *
 * The reader asked for this, repeatedly and in the same words:
 *
 *     "the new articles must be shown on the top based on the time
 *      immediatley fetch show them on ui and unitll new come new show
 *      and move old abit down and same new always on top"
 *
 * and the measurement says why it matters. Of the newest 500 stories
 * the site holds, only 15 had been fetched in the last hour — the
 * pipeline fetches about 250 rows a pass, and most of them are the
 * archive catching up. So a feed ordered by publication time barely
 * moves: the reader reloaded and found one or two new cards at the
 * top, and everything the system had just fetched was somewhere
 * further down, ordered by when the outlets published it rather than
 * by when it arrived here.
 *
 * Ordered by arrival, the top of the page IS the last fetch. The whole
 * batch lands there, in the order it was fetched, and the previous
 * batch moves down. That is what "the maximum number of articles on
 * top until new ones arrive" means.
 *
 * THE NEW BADGE IS A DIFFERENT RULE
 * ----------------------------------
 * A badge that marks an hour-old story as NEW is worse than no badge:
 * the reader stops believing it. The badge stays on PUBLICATION
 * recency — about twenty minutes — and the reader said so explicitly:
 * "the new must be of near by 20min +- other all old doonot highliht
 * them". So the list is ordered by arrival and the badge is about
 * publication, and the two never disagree about what "new" means.
 */

/** Where the next page of the feed starts. */
export interface FeedCursor {
  /**
   * When WE fetched the story.
   *
   * The cursor has to address the ordering the query actually runs.
   * The feed is ordered by arrival, so the cursor is on arrival time
   * and the id.
   */
  ingested_at: string;
  /** The tiebreaker, so a page boundary inside a run of identical
   *  timestamps cannot skip or repeat rows. */
  id: string;
}

/**
 * The columns the feed is ordered by, in order.
 *
 * `id` is the tiebreaker. Several rows are routinely ingested inside
 * the same instant, so without a second sort key PostgreSQL is free
 * to return that run in any order it likes — which makes page
 * boundaries inside it skip and repeat rows.
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
 * first, the id as the tiebreaker. It has to agree with the query's
 * ordering, because the two are the same decision seen from two
 * sides.
 */
export function byArrival<
  T extends {
    id?: string;
    ingested_at?: string | null;
    published_at?: string | null;
    source_count?: number | null;
  },
>(a: T, b: T): number {
  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  // The tiebreaker, and the same one the query uses. Without it two
  // rows fetched in the same instant could swap places between the
  // server's answer and the browser's sort, and the page would
  // visibly reshuffle.
  const idA = a.id ?? "";
  const idB = b.id ?? "";
  if (idA !== idB) return idB < idA ? -1 : 1;

  // The last tiebreaker, and it only ever fires when two rows carry
  // the same id, which cannot happen. It is here so the comparator is
  // total.
  return (b.source_count ?? 0) - (a.source_count ?? 0);
}
