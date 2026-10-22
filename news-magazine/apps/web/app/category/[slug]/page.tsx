import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { LiveArticleFeed } from "@/components/LiveArticleFeed";
import { ReelsFeed } from "@/components/ReelsFeed";
import { getCategories, getLatestArticles } from "@/lib/news";
import { getReels } from "@/lib/reels";
import { SITE_NAME, SITE_URL } from "@/lib/site";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  const categories = await getCategories();
  return categories.map((category) => ({ slug: category.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const categories = await getCategories();
  const category = categories.find(
    (item) => item.slug.toLowerCase() === slug.toLowerCase()
  );

  if (!category) {
    return { title: "Category not found" };
  }

  const name = category.name_ne ?? category.name_en;
  const description = `Latest ${category.name_en.toLowerCase()} news from across Nepal, aggregated from more than 20 Nepali outlets and deduplicated.`;

  return {
    // The site name comes from layout.tsx's title.template — appending
    // it here duplicated it in every category title.
    title: `${category.name_en} news`,
    description,
    alternates: { canonical: `/category/${category.slug}` },
    openGraph: {
      title: `${name} — latest news`,
      description,
      type: "website",
      url: `${SITE_URL}/category/${category.slug}`,
      siteName: SITE_NAME,
    },
    twitter: {
      card: "summary_large_image",
      title: `${name} — latest news`,
      description,
    },
  };
}

/**
 * Category page.
 *
 * Stage 1 validated the slug against a hardcoded CATEGORY_NAMES map and
 * then returned `<LiveNewsFeed category={slug} />` — the Gemini-
 * fabricated feed — before ever reaching the ~300 lines of database
 * query code that followed the return. Those lines were unreachable, so
 * no category page ever showed a real article, and any slug outside the
 * six hardcoded names 404'd even though the categories table holds
 * eleven.
 *
 * Now it reads the categories table for the slug and queries the
 * deduplicated articles for that category.
 */
export default async function CategoryPage({ params }: Props) {
  const { slug: rawSlug } = await params;
  const slug = rawSlug?.toLowerCase();

  if (!slug) notFound();

  const categories = await getCategories();
  const category = categories.find(
    (item) => item.slug.toLowerCase() === slug
  );

  if (!category) notFound();

  /*
   * Reels are a category in the nav, the footer and the sitemap like any
   * other — migration 0014 inserts the row — but they are not articles.
   * Articles are never filed under it, so the normal query below would
   * return an empty feed and this page would claim, on every visit, that
   * there were no stories in Reels. The one category whose body is not
   * a grid gets its own body.
   *
   * This is the ONLY branch that reads differently. Everything after it
   * is untouched, and no other slug can reach it.
   */
  if (slug === "reels") {
    const reels = await getReels();

    if (reels.reels.length === 0) {
      return (
        <main className="min-h-screen">
          <section className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <EmptyState
              title={
                reels.configured
                  ? "No reels yet"
                  : "Cannot reach the database"
              }
              description={
                // The migration has not been applied. That is not a
                // failure and must not read like one, so it says what to
                // do instead of apologising for a missing feature.
                reels.note ??
                reels.error ??
                (reels.configured
                  ? "Vertical video is ingested separately from written news, on its own schedule. Nothing has been published yet — this page fills itself as soon as the first batch lands."
                  : "Configure Supabase to see reels here.")
              }
            />
          </section>
        </main>
      );
    }

    // The news that drifts either side of the feed. Fetched only once
    // there are reels to put it beside.
    const side = await getLatestArticles();

    return (
      /*
        The Reels page is a full-viewport experience, and it is the one
        route on the site that must not have a page-level scrollbar.

        `min-h-screen` was wrong here. The header is `h-16` and sticky,
        and a sticky element still occupies space in the flow, so the
        document was header (64px) + main (100vh) tall - 64px past the
        viewport. The page therefore scrolled, and the scrollbar sat on
        the right edge of the screen next to the drifting news column,
        which is exactly where a reader expects nothing to be.

        The height is now the viewport minus the header, and the content
        inside is sized against that rather than against the viewport
        again. `overflow-hidden` is belt and braces: it guarantees the
        route cannot grow a page scrollbar even if a child is taller
        than expected, and the middle column scrolls itself.

        The other category routes are ordinary scrolling pages and are
        untouched.
      */
      <main className="h-[calc(100dvh-4rem)] overflow-hidden">
        <ReelsFeed reels={reels.reels} sideArticles={side.articles} />
      </main>
    );
  }

  const feed = await getLatestArticles({ limit: 24, categorySlug: slug! });

  return (
    <main className="min-h-screen">
      <section className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/*
          No page-level title block. The nav already marks the active
          category and the browser tab carries the name; repeating
          "World / विश्व · World" above the grid was pure duplication.
        */}
        {feed.articles.length === 0 ? (
          <EmptyState
            title={
              feed.configured
                ? "No stories in this category yet"
                : "Cannot reach the database"
            }
            description={
              feed.error ??
              (feed.configured
                ? `Nothing published in the last 48 hours was categorised as ${category.name_en}. The categoriser assigns categories by keyword, so coverage appears as new stories arrive.`
                : "Configure Supabase to see live news here.")
            }
          />
        ) : (
          // The same live feed the homepage uses, not a fixed grid.
          //
          // This page used to render ImagelessGrid - twenty-four cards
          // and then nothing. It was the only page on the site that
          // ended, so a reader who wanted the rest of a category had
          // nowhere to go, and the newest story in a busy category
          // could sit below the fold with nothing to pull it up.
          //
          // LiveArticleFeed is the homepage's feed: newest first, by
          // arrival rather than publication time, growing as the
          // reader scrolls, and it never leaves the category. The
          // empty-image handling it does is the same handling
          // ImagelessGrid did, and better: a story whose stored image
          // 404s is dropped from the grid and the next one slides up
          // into its place.
          <LiveArticleFeed
            initialArticles={feed.articles}
            initialCursor={feed.nextCursor}
            category={category.slug}
            emptyState={{
              title: "No stories in this category yet",
              description: `Nothing published in the last 48 hours was categorised as ${category.name_en}. The categoriser assigns categories by keyword, so coverage appears as new stories arrive.`,
            }}
          />
        )}
      </section>
    </main>
  );
}