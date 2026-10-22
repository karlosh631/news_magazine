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

/** Newest first — by when we ARRIVED at the story, not when it was published. */
export function byNewest<T extends FeedOrder>(a: T, b: T): number {
  // `ingested_at` first: a story moves up ONCE, when it arrives, and then
  // stays where it is. A story published two minutes ago that we fetched ten
  // minutes ago has been on the page the whole time and must not leap back to
  // the top on every poll. Publication time breaks ties, and orders rows
  // that have no arrival time at all.
  const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
  const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
  if (arrivedB !== arrivedA) return arrivedB - arrivedA;

  const left = a.published_at ? Date.parse(a.published_at) : 0;
  const right = b.published_at ? Date.parse(b.published_at) : 0;
  if (right !== left) return right - left;
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

  return [...byId.values()].sort(byNewest);
}
