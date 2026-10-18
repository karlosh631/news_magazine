import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { LiveArticleFeed } from "@/components/LiveArticleFeed";
import { getBreakingNews, getLatestArticles } from "@/lib/news";

export const dynamic = "force-dynamic";

/**
 * Homepage.
 *
 * Reads the deduplicated articles table, newest first. Stage 1 rendered
 * only <LiveNewsFeed>, which polled /api/live-news — three hardcoded
 * feeds passed through Gemini — so the entire 20+ portal ingestion
 * pipeline was invisible to visitors and the site showed fabricated
 * headlines instead.
 */
export default async function HomePage() {
  const [feed, breaking] = await Promise.all([
    getLatestArticles({ limit: 24 }),
    getBreakingNews(10),
  ]);

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      {breaking.length > 0 && <BreakingNewsTicker articles={breaking} />}

      <LiveArticleFeed
        initialArticles={feed.articles}
        emptyState={{
          title: feed.configured ? "No stories yet" : "Cannot reach the database",
          description:
            feed.error ??
            (feed.configured
              ? "The ingestion worker has not published anything yet. Start it with `python -m app.worker` in apps/ingestion, or POST /api/sync to force a first run."
              : "Configure Supabase to see live news here."),
        }}
      />
    </main>
  );
}