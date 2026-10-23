import { getLatestArticles } from "@/lib/news";
import { SITE_NAME, SITE_URL } from "@/lib/site";

/**
 * /feed.xml — the latest stories as RSS 2.0.
 *
 * WHY A SITE THAT HAS A JSON API NEEDS A FEED TOO
 * ------------------------------------------------
 * /api/live-news answers the same question in JSON, and JSON is the
 * better answer for a program. But the oldest and most widely
 * supported way for a program to ask "what is new here" is still a
 * feed, and a feed needs no API key, no documentation and no client
 * library: a reader's aggregator, a script with `curl`, or an agent
 * that has been told to look for one will all find it.
 *
 * It is also what /llms.txt points at, and a link there that goes
 * nowhere is worse than no link.
 *
 * The feed carries the same rows the homepage shows, in the same
 * order, and it says where each story came from — a story without its
 * outlet is a story nobody can check.
 */

/** How many stories one feed carries. */
const FEED_SIZE = 30;

/** Escape the five characters XML cares about. */
function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export async function GET() {
  const feed = await getLatestArticles({ limit: FEED_SIZE });

  const items = feed.articles.map((article) => {
    const url = `${SITE_URL}/article/${article.slug}`;
    const published = article.published_at ?? new Date().toISOString();
    const description =
      article.excerpt ??
      (article.summary_points ?? []).join(" ") ??
      "";

    return [
      "    <item>",
      `      <title>${escapeXml(article.headline)}</title>`,
      `      <link>${escapeXml(url)}</link>`,
      `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
      `      <pubDate>${new Date(published).toUTCString()}</pubDate>`,
      `      <description>${escapeXml(description)}</description>`,
      article.source_name_snapshot
        ? `      <source url="${escapeXml(article.source_article_url ?? SITE_URL)}">${escapeXml(
            article.source_name_snapshot
          )}</source>`
        : null,
      "    </item>",
    ]
      .filter(Boolean)
      .join("\n");
  });

  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    "  <channel>",
    `    <title>${escapeXml(SITE_NAME)}</title>`,
    `    <link>${escapeXml(SITE_URL)}</link>`,
    `    <description>${escapeXml(
      "Live Nepali and English news from more than 20 outlets across Nepal, newest first."
    )}</description>`,
    "    <language>ne</language>",
    `    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>`,
    `    <atom:link href="${escapeXml(
      `${SITE_URL}/feed.xml`
    )}" rel="self" type="application/rss+xml"/>`,
    ...items,
    "  </channel>",
    "</rss>",
    "",
  ].join("\n");

  return new Response(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      // A feed is only useful if it is fresh, and the CDN holding it
      // for a minute is the same trade the rest of the site makes.
      "Cache-Control": "public, max-age=60, s-maxage=60, stale-while-revalidate",
    },
  });
}
