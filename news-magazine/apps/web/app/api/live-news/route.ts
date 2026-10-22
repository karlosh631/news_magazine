import { NextResponse } from "next/server";

import { getLatestArticles, type FeedCursor } from "@/lib/news";

/**
 * Live news feed.
 *
 * Stage 1 fetched three hardcoded RSS feeds, ran their headlines through
 * Gemini, and rendered the model's invented titles, summaries and media
 * URLs as news. There was no deduplication, so a story carried by several
 * outlets appeared once per outlet, and the synthetic `id` values
 * (`gemini-live-0-<timestamp>`) regenerated on every request so article
 * links 404'd.
 *
 * This now serves the deduplicated articles table — the same data the
 * homepage renders — so a story an outlet published appears once, with
 * its real image and a stable slug.
 */

export const dynamic = "force-dynamic";

/*
 * Never cached.
 *
 * This was `s-maxage=15, stale-while-revalidate=30`. The endpoint is
 * polled every few seconds by every open tab precisely so the page
 * reflects what the worker has just published, so any shared cache in
 * front of it — CDN or browser — can only ever serve a staler answer
 * than the database holds. A poll that returns cached news is a
 * homepage frozen at whatever was newest when the entry was stored.
 *
 * The client also sends a unique `t` on every request, so there is no
 * cache key worth reusing in the first place.
 */
const NO_STORE = "no-store, no-cache, must-revalidate, max-age=0";

/** What a request gets if it does not ask for anything in particular. */
const DEFAULT_LIMIT = 24;

/**
 * The ceiling on one response.
 *
 * This used to be 50, which meant the feed stopped at 50 no matter how
 * old the reader wanted to scroll. It is not unlimited now either, and
 * deliberately so: `getLatestArticles` fetches `limit * DEDUP_HEADROOM`
 * rows and runs the pairwise headline comparison over every one of them,
 * so a single request for the whole table is not a large response, it is
 * a large amount of main-thread and server CPU handed to whoever typed
 * the URL. 500 is twenty-one pages of 24, several times the whole feed
 * today, and the client never asks for more than 24 in one go.
 *
 * This is a ceiling per REQUEST, not a ceiling on the feed: the client
 * walks the cursor and can reach every row the database holds.
 */
const MAX_LIMIT = 500;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * A strict ISO-8601 instant.
 *
 * Not `Date.parse`, and that is the point. Both cursor values are
 * interpolated straight into a PostgREST `or=(...)` filter, so a caller
 * that could put a comma or a parenthesis in them could rewrite the
 * filter — turning a page request into a full-table read, or into a
 * different filter entirely. These two patterns admit only a timestamp
 * and a UUID, so there is nothing left to inject.
 */
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

/** `limit`, clamped. Garbage in means the default, not an error. */
function readLimit(raw: string | null): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_LIMIT;
  return Math.min(Math.floor(parsed), MAX_LIMIT);
}

/**
 * Where the reader left off, from `published_at` + `after_id`.
 *
 * Absent on the first request, and rejected outright when either half is
 * missing or malformed — a cursor that silently degrades to "the newest
 * page" would make the reader's second page repeat the first.
 *
 * THE CURSOR ADDRESSES THE ORDERING THE QUERY RUNS
 * -------------------------------------------------
 * The feed is ordered by publication time, so the cursor is on
 * publication time. A cursor on any other column addresses a
 * different ordering than the query runs, and applying it would skip
 * rows silently — which is worse than having no cursor at all.
 */
function readCursor(url: URL): FeedCursor | null {
  const id = url.searchParams.get("after_id");
  const publishedAt = url.searchParams.get("published_at");

  if (!id || !publishedAt) return null;
  if (!UUID.test(id) || !TIMESTAMP.test(publishedAt)) return null;

  // Carried verbatim, never round-tripped through a Date: PostgreSQL
  // timestamps carry microseconds and `Date` carries milliseconds, so
  // re-serialising would truncate the cursor and drop every row in the
  // truncated sliver.
  return { id, published_at: publishedAt };
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const category = (url.searchParams.get("category") || "all").toLowerCase();
  const limit = readLimit(url.searchParams.get("limit"));
  const cursor = readCursor(url);
  const languageParam = url.searchParams.get("language");
  const language =
    languageParam === "ne" || languageParam === "en" ? languageParam : undefined;
  // An exact count is a second query. The client asks for it while paging
  // through the archive, where it decides whether "Load more" is a lie,
  // and leaves it off the ten-second poll, which does not need it.
  const withTotal = url.searchParams.get("total") === "1";

  const feed = await getLatestArticles({
    limit: category === "all" ? limit : limit * 2,
    categorySlug: category === "all" ? undefined : category,
    language,
    cursor,
    withTotal,
  });

  if (feed.error && feed.articles.length === 0) {
    return NextResponse.json(
      { error: feed.error, articles: [] },
      { status: feed.configured ? 503 : 200, headers: { "Cache-Control": NO_STORE } }
    );
  }

  // `served_at` is the moment THIS response was computed, and
  // `newest_age_minutes` is how old the freshest article in it is.
  //
  // They exist to settle a question that could not be answered from the
  // code or the client: whether a reader is looking at a live response
  // or a cached one. If a page shows `served_at` minutes or hours in the
  // past, something in front of this route is serving a cached body
  // despite `Cache-Control: no-store`, and the numbers above it are
  // meaningless. If `served_at` is current and `newest_age_minutes` is
  // small, the feed is healthy and the problem is elsewhere.
  const newestMs = feed.articles.reduce((best, article) => {
    const parsed = article.published_at ? Date.parse(article.published_at) : NaN;
    return Number.isNaN(parsed) ? best : Math.max(best, parsed);
  }, 0);

  return NextResponse.json(
    {
      articles: feed.articles,
      configured: feed.configured,
      language: language ?? "all",
      // Where the next page starts. Null means there is nothing below
      // this one — the whole feed has been read.
      next_cursor: feed.nextCursor,
      has_more: feed.nextCursor !== null,
      // Approximate: it counts rows, and some of those become cards only
      // after deduplication removes their twins.
      total: feed.total,
      served_at: new Date().toISOString(),
      newest_article_at: newestMs ? new Date(newestMs).toISOString() : null,
      newest_age_minutes: newestMs
        ? Math.round((Date.now() - newestMs) / 60000)
        : null,
    },
    { headers: { "Cache-Control": NO_STORE } }
  );
}