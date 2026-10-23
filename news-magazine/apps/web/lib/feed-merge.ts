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
  // The newest FETCHED first, exactly as the server orders it (see
  // `newestFirst` in lib/news.ts). The two have to agree or the page
  // visibly reshuffles on the first poll after every load.
  //
  // The reader asked for this in the same words several times: "the new
  // articles must be shown on the top based on the time immediatley
  // fetch show them on ui and unitll new come new show and move old
  // abit down and same new always on top".
  //
  // The measurement says why it matters. A pass fetches about 250 rows
  // and most of them are the archive catching up, so of the newest 500
  // stories the site holds only 15 had been fetched in the last hour.
  // Ordered by publication time the top of the page barely moved: the
  // reader reloaded, found one or two new cards, and everything the
  // system had just fetched was further down.
  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  // The tiebreaker: the same one the query uses, so two rows fetched in
  // the same instant cannot swap places between the server's answer
  // and the browser's sort.
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
    A row is an arrival when it is NOT on the page yet AND it was
    fetched AFTER the newest thing already on the page.

    The second half is what makes a whole batch land at once. A fetch
    brings two hundred and fifty rows, every one of them fetched a
    moment ago, and the page's newest row was fetched before the batch
    started - so every row of the batch is an arrival, and the whole
    batch goes on top, in the order it was fetched. The reader asked
    for exactly that, in the same words several times:

        the new articles must be shown on the top based on the time
        immediatley fetch show them on ui and unitll new come new show
        and move old abit down and same new always on top

    The rule used to be "newer than the newest row on the page" by
    PUBLICATION time, and it was wrong twice over. A batch of fifteen
    stories published over the last hour showed ONE of them at the top,
    because fourteen of them were older than the story already on the
    page - the reader's complaint, verbatim: "only showing 1,2
    articles on top on each fecth".

    The archive is not a special case any more. It is fetched in the
    same breath as the news, it is ordered by the same clock, and it
    arrives at the top like everything else. What the reader reads is
    the newest NEWS, because the page says when each story was
    published - and what the reader SEES arriving is the batch the
    system just fetched.
  */
  const newestArrivalOnPage = current[0]?.ingested_at
    ? Date.parse(current[0].ingested_at)
    : 0;

  const arrivedAfterThePage = (article: T) => {
    if (!article.ingested_at) return false;
    const arrived = Date.parse(article.ingested_at);
    if (Number.isNaN(arrived)) return false;
    return arrived > newestArrivalOnPage;
  };

  const arrivals = fresh
    .filter((article) => !known.has(article.id) && arrivedAfterThePage(article))
    .sort(byNewest);

  const kept = current.map((article) => byId.get(article.id) ?? article);

  // Everything else - the page's own rows and the archive the poll
  // brought back - in one list, newest news first.
  const arrivalIds = new Set(arrivals.map((article) => article.id));
  const rest = fresh.filter((article) => !arrivalIds.has(article.id));
  const restIds = new Set(rest.map((article) => article.id));
  const tail = [
    ...rest,
    ...kept.filter((article) => !restIds.has(article.id)),
  ].sort(byNewest);

  if (arrivals.length === 0) {
    // Nothing arrived inside the window. The union is still the union:
    // a story the page had not mounted takes its place in the order
    // rather than being dropped. The first early return above already
    // handled the case where the poll found nothing at all, and that
    // one hands back the SAME array so React skips the re-render.
    return tail;
  }

  // The arrivals on top, pushing the page down.
  return [...arrivals, ...tail];
}
