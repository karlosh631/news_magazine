import type { Metadata } from "next";

import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { LiveArticleFeed } from "@/components/LiveArticleFeed";
import { getBreakingNews, getLatestArticles } from "@/lib/news";
import { SITE_LOGO, SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";

/**
 * The homepage is regenerated at most once a minute.
 *
 * It used to be `dynamic = "force-dynamic"`, which meant every single
 * request re-rendered the whole page and re-read the whole feed from
 * the database, and the reader waited for all of it before the first
 * byte of HTML arrived. Measured against the live site, that was
 * 1.9 seconds of time-to-first-byte on the homepage and 2.9 on a
 * category page — the single largest thing keeping the Performance
 * score at 40.
 *
 * A minute is the trade the site asked for: "a one-minute-old cache is
 * fine, a five-minute-old one is not". The newest story is at most a
 * minute old, and the client-side poll on top of this (every ten
 * seconds) merges in anything newer the moment the worker publishes
 * it, so the page a reader is looking at still moves.
 *
 * `revalidate` and `dynamic` are alternatives, not a pair: a route
 * that exports `revalidate` is a static route that is regenerated in
 * the background, and the CDN serves the HTML it already has.
 */
// The home page is NEVER served from a cache.
//
// It used to be regenerated every sixty seconds, and the reader saw
// why that is not enough: a hard refresh in a private window showed a
// page that was more than an hour old, with an hour-old story at the
// top, because the regeneration had failed (the database connection
// is not always there) and the stale page was still being served.
//
// A live news page that can be an hour stale is not a live news page.
// The page is built per request instead, and the reader's own browser
// holds it for the few seconds it takes to draw it.
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
    getLatestArticles({ limit: 36 }),
    getBreakingNews(10),
  ]);

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(structuredData) }}
      />

      {/*
        The page's name.

        The homepage has no visible title block — the breaking-news
        ticker and the grid say what the page is — but a page with no
        <h1> is a page whose subject a screen reader and a crawler
        both have to guess, and axe-core fails it for
        `page-has-heading-one`. It is visually hidden rather than
        absent.
      */}
      <h1 className="sr-only">Latest news from across Nepal</h1>

      {breaking.length > 0 && <BreakingNewsTicker articles={breaking} />}

      <LiveArticleFeed
        initialArticles={feed.articles}
        initialCursor={feed.nextCursor}
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