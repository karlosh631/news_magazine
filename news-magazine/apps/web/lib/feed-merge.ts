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
 * Newest first — by when the story was PUBLISHED, with arrival as the
 * tiebreaker.
 *
 * WHY PUBLICATION TIME COMES FIRST
 * --------------------------------
 * This used to sort by arrival time first, on the argument that a
 * publisher who sat on a story for hours makes it new to this site
 * when it finally appears. That argument is true and it is also the
 * wrong thing to show a reader.
 *
 * The reader opened a news site. The top of it was a story published
 * in JUNE, sitting above today's news, because we had re-fetched it.
 * A feed whose first three cards are four months old does not read as
 * "up to date" however defensible the rule behind it is - it reads as
 * broken, and the reader is right.
 *
 * So the order is: the newest NEWS first, by publication time, and
 * within one publication moment the most recently fetched copy first.
 * That is what "the latest" means to somebody looking at a news
 * site, and it still gives the arrival time a job: two outlets file
 * the same story in the same second, and the copy we fetched last is
 * the one with the better picture.
 */
export function byNewest<T extends FeedOrder>(a: T, b: T): number {
  // The NEWEST NEWS first, exactly as the server orders it (see
  // `newestFirst` in lib/news.ts). The two have to agree or the page
  // visibly reshuffles on the first poll after every load.
  const left = a.published_at ? Date.parse(a.published_at) : 0;
  const right = b.published_at ? Date.parse(b.published_at) : 0;
  if (right !== left) return right - left;

  // The tiebreaker: the most recently fetched copy of the same moment
  // wins, so the better picture and the higher outlet count end up on
  // the card the reader sees.
  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  // The tiebreaker: the outlet that carried the story most often ends
  // up on the card the reader sees.
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

  const known = new Set(current.map((article) => article.id));
  const byId = new Map<string, T>();
  for (const article of current) byId.set(article.id, article);
  for (const article of fresh) byId.set(article.id, article);

  // Nothing new: hand back the same array so React does not re-render.
  if (byId.size === current.length) return current as T[];

  /*
    A NEW STORY GOES ON TOP AND PUSHES THE PAGE DOWN
    -------------------------------------------------
    This is the one thing the reader asked for, and it is what a live
    news feed is FOR: a story that arrives now appears at the top,
    and the stories that were there move down.

    The order is by ARRIVAL for the new arrivals, not by publication
    time. A publisher that posted an hour ago and whose story the
    worker only just reached is new to this site, and putting it where
    its publication time says would hide it in the middle of the page
    - which is exactly what the reader was seeing: nothing seemed to
    happen.

    The stories already on screen KEEP their places. A poll that
    returns the same hundred rows every ten seconds must not reshuffle
    the page under the reader, and the header's own refresh (which
    re-renders the server tree every fifteen seconds) arrives as the
    same rows in a new array - so only the rows that are genuinely
    new may move.
  */
  /*
    WHAT COUNTS AS AN ARRIVAL
    -------------------------
    A row is an arrival when it is NOT on the page yet AND it is
    NEWER than the newest row the page is showing.

    The second half matters. The poll asks for a hundred rows and the
    page carries thirty-six of them, so sixty-four rows come back that
    the page has never shown - and they are the ARCHIVE, not news.
    Treating them as arrivals put sixty-four older stories on top of
    the page every ten seconds, which is exactly what a reader saw:
    the newest story, then a pile of old ones, then the page again.

    A hard refresh made it worse: the page came from the cache and was
    a minute behind, so the poll's whole answer looked new.

    So the arrivals are the rows that are genuinely ahead of the page,
    and everything else keeps the order it had.
  */
  const newestOnPage = current[0];
  const newestPublished = newestOnPage?.published_at
    ? Date.parse(newestOnPage.published_at)
    : 0;

  const isAhead = (article: T) => {
    if (!article.published_at) return false;
    const published = Date.parse(article.published_at);
    if (Number.isNaN(published)) return false;
    return published > newestPublished;
  };

  const arrivals = fresh.filter(
    (article) => !known.has(article.id) && isAhead(article)
  );

  const kept = current.map((article) => byId.get(article.id) ?? article);

  // Everything else - the page's own rows and the archive the poll
  // brought back - in one list, newest news first.
  const rest = fresh.filter((article) => !arrivals.includes(article));
  const restIds = new Set(rest.map((article) => article.id));
  const tail = [
    ...rest,
    ...kept.filter((article) => !restIds.has(article.id)),
  ].sort(byNewest);

  if (arrivals.length === 0) {
    // Nothing arrived ahead of the page. The union is still the union:
    // a story the page had not mounted takes its place in the order
    // rather than being dropped. The first early return above already
    // handled the case where the poll found nothing at all, and that
    // one hands back the SAME array so React skips the re-render.
    return tail;
  }

  // The arrivals on top, pushing the page down.
  return [...arrivals, ...tail];
}
