import type { Metadata } from "next";

import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { LiveArticleFeed } from "@/components/LiveArticleFeed";
import { getBreakingNews, getLatestArticles } from "@/lib/news";
import { SITE_LOGO, SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
};

/**
 * Site-level structured data.
 *
 * The homepage carried no JSON-LD at all, so search engines had no
 * statement of what this site *is* and no way to associate the search
 * box with it. WebSite + SearchAction is what makes the site's own search
 * eligible to be surfaced as a sitelinks searchbox.
 */
const structuredData = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "NewsMediaOrganization",
      "@id": `${SITE_URL}/#organization`,
      name: SITE_NAME,
      url: SITE_URL,
      logo: {
        "@type": "ImageObject",
        url: SITE_LOGO,
        width: 192,
        height: 192,
      },
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_URL}/#website`,
      name: SITE_NAME,
      url: SITE_URL,
      publisher: { "@id": `${SITE_URL}/#organization` },
      inLanguage: ["ne", "en"],
      potentialAction: {
        "@type": "SearchAction",
        target: {
          "@type": "EntryPoint",
          urlTemplate: `${SITE_URL}/search?q={search_term_string}`,
        },
        "query-input": "required name=search_term_string",
      },
    },
  ],
};

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
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(structuredData) }}
      />

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