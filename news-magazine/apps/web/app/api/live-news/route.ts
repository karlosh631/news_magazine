import { NextResponse } from "next/server";

import { getLatestArticles } from "@/lib/news";

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

export async function GET(request: Request) {
  const url = new URL(request.url);
  const category = (url.searchParams.get("category") || "all").toLowerCase();
  const limit = Math.min(Number(url.searchParams.get("limit") ?? 24) || 24, 50);
  const languageParam = url.searchParams.get("language");
  const language =
    languageParam === "ne" || languageParam === "en" ? languageParam : undefined;

  const feed = await getLatestArticles({
    limit: category === "all" ? limit : limit * 2,
    categorySlug: category === "all" ? undefined : category,
    language,
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
      served_at: new Date().toISOString(),
      newest_article_at: newestMs ? new Date(newestMs).toISOString() : null,
      newest_age_minutes: newestMs
        ? Math.round((Date.now() - newestMs) / 60000)
        : null,
    },
    { headers: { "Cache-Control": NO_STORE } }
  );
}