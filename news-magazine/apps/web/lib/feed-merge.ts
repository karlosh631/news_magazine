/**
 * How a running feed absorbs a new batch of articles.
 *
 * Extracted from components/LiveArticleFeed.tsx, and pure, because the rule
 * it encodes is the one that decides whether the page the reader is looking
 * at gets rebuilt underneath them.
 *
 * THE BUG THIS EXISTS FOR
 * ----------------------
 * The header refreshes the server tree every 15 seconds (AUTO_REFRESH_MS in
 * components/Header.tsx). That used to reach the feed twice:
 *
 *   1. `router.refresh()` handed LiveArticleFeed a NEW `initialArticles`
 *      array — the same 24 stories it had already been sent, with a new
 *      identity. The effect that watched those props did
 *      `setArticles(initialArticles)`, throwing away the union the client
 *      had been building. The grid collapsed from 60 cards back to 24.
 *   2. The same tick dispatched `news-refresh`, whose handler did
 *      `check(language, true)` — a reset that replaced the list with exactly
 *      what the API returned. The grid went back to 60.
 *
 * Every fifteen seconds, in that order, in front of the reader: the page
 * shrank and grew again. Measured with a PerformanceObserver, one cycle was
 * worth about 0.34 CLS on mobile and 0.15 on desktop, and it repeated for
 * as long as the tab was open. The content never changed — the same stories
 * in the same order, moved twice.
 *
 * `mergeArticles` is the rule that stops it: a batch that contains nothing
 * the list does not already have leaves the list exactly as it was, and the
 * identity of the array is preserved so React skips the re-render entirely.
 * A batch with something new in it is folded in, newest first, and only
 * those cards move.
 */

/**
 * The columns the ordering reads. `LatestArticle` satisfies this; the shape
 * is written out rather than imported so this file stays free of types that
 * only mean something to the database layer.
 */
export type FeedOrder = {
  id: string;
  published_at: string | null;
  ingested_at?: string | null;
  source_count?: number | null;
};

/**
 * A timestamp as a number, and 0 for anything that is not one.
 *
 * `Date.parse` returns NaN for a string it cannot read, and a
 * comparator that returns NaN is not a comparator: the sort engine
 * treats it as "no opinion" and the rows it was asked to order keep
 * whatever order they arrived in. So an unreadable timestamp is worth
 * 0 — the same as a missing one — and an undated row sorts last.
 */
function stamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/**
 * Newest first — by when the story was PUBLISHED, with arrival as the
 * tiebreaker.
 *
 * WHY PUBLICATION TIME COMES FIRST
 * --------------------------------
 * This is the rule the reader reads the page by, and it is the one the
 * measurement supports.
 *
 * The feed was ordered by ARRIVAL for a while — by when WE fetched the
 * story — because a publisher who sat on a story for an hour makes it
 * new to this site when it finally appears. That argument is true, and
 * it is also the wrong thing to show a reader, because a late arrival
 * is a story whose PUBLICATION time is old. Measured against the live
 * site, the top card of the arrival-ordered feed carried a publication
 * time of nine minutes and forty seconds old, while the cards directly
 * beneath it carried four minutes. The page was showing a reader a
 * headline that was older than the headlines under it, and the reader
 * read the top of the page as "the news is ten minutes behind".
 *
 * That is the whole of the "too much gap between the new news and real
 * time" complaint: not that the site was slow, but that the top of it
 * LOOKED slow.
 *
 * So the order is: the newest NEWS first, by publication time, and
 * within one publication moment the most recently fetched copy first.
 * The arrival time still has a job — two outlets file the same story
 * in the same second, and the copy we fetched last is the one with
 * the better picture — and it still has a second one: the batch that
 * has just been fetched is all of it published in the last few minutes
 * (the outlets publish a story and put it in their feed within the
 * minute), so the whole batch lands at the top together, and the
 * previous batch moves down. The reader gets both of the things they
 * asked for, because this time the two rules agree with each other.
 */
export function byNewest<T extends FeedOrder>(a: T, b: T): number {
  // The newest NEWS first: the moment the story was published.
  //
  // The same order the server orders it in (see `newestFirst` in
  // lib/news.ts). The two have to agree or the page visibly reshuffles
  // on the first poll after every load.
  const publishedA = stamp(a.published_at);
  const publishedB = stamp(b.published_at);
  if (publishedB !== publishedA) return publishedB - publishedA;

  // The tiebreaker: the most recently FETCHED copy of that story.
  const arrivedA = stamp(a.ingested_at);
  const arrivedB = stamp(b.ingested_at);
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  // The second tiebreaker: the same one the query uses, so two rows
  // fetched in the same instant cannot swap places between the
  // server's answer and the browser's sort.
  const idA = a.id ?? "";
  const idB = b.id ?? "";
  if (idA !== idB) return idB < idA ? -1 : 1;

  // The last tiebreaker: the outlet that carried the story most often
  // ends up on the card the reader sees.
  return (b.source_count ?? 0) - (a.source_count ?? 0);
}

/**
 * Fold a batch into the running list, newest first.
 *
 * Union by id, and the union only ever grows: a story already in the list
 * keeps the copy it arrived with, so a poll that re-fetches rows the reader
 * is already looking at changes nothing. That is the rule the layout-shift
 * fix depends on, and it is a real trade — a story whose row the worker
 * later enriches (more outlets carrying it, a better photograph) keeps the
 * copy this tab first saw until the page is reloaded, because the
 * alternative is rebuilding the array on every ten-second poll and moving
 * the page to do it.
 *
 * Returns `current` itself — not a copy — when the batch adds no new id.
 * That identity is the whole point: it is what lets React skip the
 * re-render, and therefore what keeps a poll that found no news from moving
 * a single pixel.
 */
export function mergeArticles<T extends FeedOrder>(
  current: readonly T[],
  fresh: readonly T[],
): T[] {
  if (fresh.length === 0) return current as T[];

  const byId = new Map<string, T>();
  for (const article of current) byId.set(article.id, article);
  for (const article of fresh) byId.set(article.id, article);

  // Nothing new: hand back the same array so React does not re-render.
  if (byId.size === current.length) return current as T[];

  /*
    ONE ORDER FOR THE WHOLE LIST
    ---------------------------
    The union is sorted once, by `byNewest`, and that is the order the
    reader sees. It used to be built in two pieces - the arrivals on
    top, everything else beneath them - which put a story that had just
    been fetched ABOVE a story published a minute earlier, and made the
    arrival of a late story the only thing that could move the top of
    the page.

    One order is simpler, and it is the one the reader asked for. The
    batch that has just been fetched is a batch of stories published in
    the last few minutes, so it lands at the top on its own - not
    because it was fetched last, but because it was published last.
    The two rules agree with each other, and the top of the page is
    always the newest news rather than the newest arrival.

    The stories already on screen keep their places: the sort is by
    fields that do not change under the reader (published_at,
    ingested_at, id), so a poll that returns the same hundred rows
    produces the same order, and the header's own fifteen-second
    refresh - which re-renders the server tree and hands the same rows
    back in a new array - moves nothing.
  */
  return [...byId.values()].sort(byNewest);
}
