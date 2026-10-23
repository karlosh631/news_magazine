import { SITE_NAME, SITE_URL } from "@/lib/site";

/**
 * /llms.txt — a short, machine-readable description of this site.
 *
 * WHAT THIS IS FOR
 * ----------------
 * A growing number of tools answer questions by fetching a site's
 * `/llms.txt` first: a plain-text Markdown file that says what the
 * site is, what the important sections are, and where to find them.
 * It is a convention, not a standard — the specification is at
 * llmstxt.org — and it costs one request.
 *
 * The format is deliberately narrow, because the point is that a model
 * can read it without being told how:
 *
 *   # Title
 *
 *   > A one-paragraph summary, in a blockquote.
 *
 *   ## A section
 *
 *   - [A link](https://example.com/a): what it is.
 *
 * The sections here are the ones a reader — or an agent answering on
 * a reader's behalf — actually wants: the categories, the ways to get
 * the latest stories as data, and the places where the site's own
 * metadata lives.
 *
 * IT IS NOT A SITEMAP
 * -------------------
 * There is already a sitemap, at /sitemap.xml, and it lists every
 * article. This file does not repeat it: a file with two thousand
 * links is not a summary, and a summary nobody reads is worse than no
 * file. What is here is the shape of the site and the handful of
 * endpoints that answer "what is new".
 */

/**
 * The categories, in the order the site puts them in the nav.
 *
 * Written out rather than read from the database on every request:
 * this file is a description of the SITE, and the site's shape
 * changes with a code change rather than with a row in a table. It is
 * also served with a long cache lifetime, and a list of categories
 * that is a minute out of date is a description of the site either
 * way.
 */
const SECTIONS = [
  { slug: "national", name: "National", blurb: "News from across Nepal" },
  { slug: "politics", name: "Politics", blurb: "Government, parties and Parliament" },
  { slug: "business", name: "Business", blurb: "Economy, trade and companies" },
  { slug: "technology", name: "Technology", blurb: "Science, technology and the internet" },
  { slug: "world", name: "World", blurb: "News from outside Nepal" },
  { slug: "reels", name: "Reels", blurb: "Short vertical video" },
  { slug: "rashifal", name: "Rashifal", blurb: "Today's almanac and the week's horoscope" },
];

function buildLlmsTxt(): string {
  const lines: string[] = [];

  lines.push(`# ${SITE_NAME}`);
  lines.push("");
  lines.push(
    `> ${SITE_NAME} is a live news aggregator for Nepal. It collects stories from more than 20 Nepali and English-language outlets, deduplicates them, and republishes them newest-first, with every story linked back to the outlet that reported it. Articles are published in Nepali or English; the reader chooses.`
  );
  lines.push("");
  lines.push(
    "Every article page carries the full story, the outlet's own key points, and a link to the original. There is no paywall and no account."
  );
  lines.push("");

  lines.push("## Sections");
  lines.push("");
  for (const section of SECTIONS) {
    lines.push(
      `- [${section.name}](${SITE_URL}/category/${section.slug}): ${section.blurb}.`
    );
  }
  lines.push("");

  lines.push("## The latest stories");
  lines.push("");
  lines.push(
    `- [Homepage](${SITE_URL}/): Every published story, newest first, and the breaking-news ticker.`
  );
  lines.push(
    `- [Latest stories as JSON](${SITE_URL}/api/live-news?limit=24): the same feed the page itself polls, as JSON. Pass \`category=<slug>\` to stay inside one section, \`limit\` for the number of rows, and \`published_at\` with \`after_id\` to page backwards through the archive.`
  );
  lines.push(
    `- [Search](${SITE_URL}/search?q=): full-text search across the published archive.`
  );
  lines.push("");

  lines.push("## About this site");
  lines.push("");
  lines.push(
    `- [Sitemap](${SITE_URL}/sitemap.xml): every published article.`
  );
  lines.push(
    `- [robots.txt](${SITE_URL}/robots.txt): what may be crawled.`
  );
  lines.push(
    `- [RSS](${SITE_URL}/feed.xml): the latest stories as a feed.`
  );
  lines.push("");

  return lines.join("\n");
}

export async function GET() {
  return new Response(buildLlmsTxt(), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      // A description of the site changes when the site changes, which
      // is a deployment rather than a request.
      "Cache-Control": "public, max-age=3600, s-maxage=86400",
    },
  });
}
