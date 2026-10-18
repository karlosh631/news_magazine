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

// Short cache: the worker publishes continuously, and this endpoint is
// polled by the client for auto-refresh.
const CACHE_MS = 15_000;
const MAX_AGE = "s-maxage=15, stale-while-revalidate=30";

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
      { status: feed.configured ? 503 : 200, headers: { "Cache-Control": MAX_AGE } }
    );
  }

  return NextResponse.json(
    { articles: feed.articles, configured: feed.configured, language: language ?? "all" },
    { headers: { "Cache-Control": MAX_AGE } }
  );
}