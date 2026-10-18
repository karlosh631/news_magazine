import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { NewsCard } from "@/components/NewsCard";
import { getCategories, getLatestArticles } from "@/lib/news";
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
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {feed.articles.map((article) => (
              <NewsCard key={article.id} article={article} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}