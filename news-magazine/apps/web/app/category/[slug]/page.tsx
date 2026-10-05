import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { createServerSupabaseClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

interface Props {
  params: Promise<{
    slug: string;
  }>;
}

interface Article {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  video_url?: string | null;
  audio_url?: string | null;
  gif_url?: string | null;
  published_at: string | null;
  source_name_snapshot: string | null;
  canonical_url: string | null;
  source_article_url: string | null;
}

const CATEGORY_NAMES: Record<string, string> = {
  national: "National",
  politics: "Politics",
  business: "Business",
  technology: "Technology",
  sports: "Sports",
  entertainment: "Entertainment",
};

const ALLOWED_CATEGORIES = Object.keys(CATEGORY_NAMES);

const FALLBACK_KEYWORDS: Record<string, string[]> = {
  national: ["nepal", "nepali", "नेपाल", "kathmandu", "राष्ट्रिय"],
  politics: ["politics", "government", "election", "parliament", "राजनीति", "सरकार"],
  business: ["business", "economy", "market", "bank", "व्यापार", "अर्थतन्त्र"],
  technology: ["technology", "tech", "software", "internet", "ai", "प्रविधि"],
  sports: ["sports", "cricket", "football", "खेल", "क्रिकेट", "फुटबल"],
  entertainment: ["entertainment", "movie", "film", "music", "मनोरञ्जन", "चलचित्र"],
};

export async function generateMetadata({
  params,
}: Props): Promise<Metadata> {
  const { slug: rawSlug } = await params;
  const slug = rawSlug?.toLowerCase();

  const name = CATEGORY_NAMES[slug];

  if (!name) {
    return {
      title: "Category | Nepal News & Magazine",
    };
  }

  return {
    title: `${name} News | Nepal News & Magazine`,
    description: `Latest ${name.toLowerCase()} news and updates from Nepal.`,
  };
}

export default async function CategoryPage({
  params,
}: Props) {
  const { slug: rawSlug } = await params;
  const slug = rawSlug?.toLowerCase();

  /*
   * ---------------------------------------------------------
   * VALIDATE CATEGORY SLUG
   * ---------------------------------------------------------
   */

  if (!slug || !ALLOWED_CATEGORIES.includes(slug)) {
    notFound();
  }

  const categoryName = CATEGORY_NAMES[slug];

  let publishedArticles: Article[] = [];

  /*
   * ---------------------------------------------------------
   * STEP 1
   * FIND CATEGORY BY SLUG
   *
   * categories:
   *   id
   *   slug
   * ---------------------------------------------------------
   */

  try {
    const db = createServerSupabaseClient();
  /*
   * ---------------------------------------------------------
   * STEP 2
   * LOAD ARTICLES USING primary_category_id
   *
   * IMPORTANT:
   *
   * Do NOT do:
   *
   *   .eq("category", slug)
   *
   * Do NOT do:
   *
   *   .eq("category_slug", slug)
   *
   * We use:
   *
   *   articles.primary_category_id
   *       =
   *   categories.id
   * ---------------------------------------------------------
   */

    let articleQuery = db
      .from("articles")
      .select("*")
      .eq("status", "published")
      .order("published_at", {
        ascending: false,
        nullsFirst: false,
      })
      .limit(100);

    const articlesResult = await articleQuery;
    if (articlesResult.error) {
      console.error(`[category/${slug}] Article query failed:`, articlesResult.error);
    } else {
      publishedArticles = (articlesResult.data ?? []) as Article[];
    }
  } catch (error) {
    console.error(`[category/${slug}] Category page data load failed:`, error);
  }

  const normalizedArticles = publishedArticles
    .filter((article) => article && typeof article === "object")
    .map((article, index) => ({
      ...article,
      id: typeof article.id === "string" ? article.id : `article-${index}`,
      slug: typeof article.slug === "string" ? article.slug : "",
      headline: typeof article.headline === "string" ? article.headline : "Untitled news article",
      excerpt: typeof article.excerpt === "string" ? article.excerpt : null,
      featured_image_url: typeof article.featured_image_url === "string" ? article.featured_image_url : null,
      video_url: typeof article.video_url === "string" ? article.video_url : null,
      audio_url: typeof article.audio_url === "string" ? article.audio_url : null,
      source_name_snapshot: typeof article.source_name_snapshot === "string" ? article.source_name_snapshot : null,
    }))
    .filter((article) => article.slug);

  const safeArticles = slug === "national"
    ? normalizedArticles
    : normalizedArticles.filter((article) => {
        const text = `${article.headline} ${article.excerpt ?? ""}`.toLocaleLowerCase();
        return (FALLBACK_KEYWORDS[slug] ?? []).some((keyword) => text.includes(keyword));
      });

  return (
    <main className="min-h-screen">
      <section className="mx-auto w-full max-w-7xl px-4 py-10 sm:px-6 lg:px-8">

        {/* -------------------------------------------------
            HEADER
        -------------------------------------------------- */}

        <div className="border-b border-gray-200 pb-7">
          <p className="mb-3 text-sm font-medium uppercase tracking-wide text-gray-600">
            Nepal News & Magazine
          </p>

          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="font-serif text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
                {categoryName} News
              </h1>

              <p className="mt-3 text-base text-gray-600">
                Latest {categoryName.toLowerCase()} news and updates.
              </p>
            </div>

            <Link
              href="/search"
              className="text-sm font-medium text-slate-900 hover:underline"
            >
              Search →
            </Link>
          </div>
        </div>

        {/* -------------------------------------------------
            EMPTY STATE
        -------------------------------------------------- */}

        {safeArticles.length === 0 ? (
          <div className="mt-10 rounded-xl border border-gray-200 bg-gray-50 px-6 py-16 text-center">
            <h2 className="font-serif text-2xl font-bold text-slate-900">
              No {categoryName.toLowerCase()} news available
            </h2>

            <p className="mt-2 text-gray-600">
              New articles will appear here automatically when they
              are published.
            </p>

            <Link
              href="/"
              className="mt-6 inline-block rounded-md bg-slate-900 px-5 py-3 text-sm font-medium text-white hover:bg-slate-800"
            >
              Back to Latest News
            </Link>
          </div>
        ) : (
          /* -------------------------------------------------
             ARTICLE GRID
          -------------------------------------------------- */

          <div className="mt-8 grid grid-cols-1 gap-x-7 gap-y-12 md:grid-cols-2 lg:grid-cols-3">

            {safeArticles.map((article) => (
              <article
                key={article.id}
                className="group min-w-0"
              >
                <ArticleImage
                  src={article.featured_image_url}
                  alt={article.headline}
                />

                {article.video_url && (
                  <video
                    src={article.video_url}
                    poster={article.featured_image_url || undefined}
                    controls
                    playsInline
                    preload="metadata"
                    className="mt-3 aspect-video w-full rounded-lg bg-black object-cover"
                  />
                )}

                {article.audio_url && (
                  <audio
                    src={article.audio_url}
                    controls
                    preload="none"
                    className="mt-3 w-full"
                  />
                )}

                <div className="mt-4">
                  <h2 className="font-serif text-2xl font-bold leading-tight text-slate-900">
                    <Link
                      href={`/article/${article.slug}`}
                      className="hover:underline"
                    >
                      {article.headline}
                    </Link>
                  </h2>

                  {article.excerpt && (
                    <p className="mt-3 line-clamp-3 text-base leading-6 text-gray-600">
                      {article.excerpt}
                    </p>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-gray-500">
                    {article.published_at && (
                      <time dateTime={article.published_at}>
                        {formatDate(article.published_at)}
                      </time>
                    )}

                    {article.source_name_snapshot && (
                      <>
                        <span aria-hidden="true">•</span>

                        <span>
                          {article.source_name_snapshot}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}

      </section>
    </main>
  );
}

/* =========================================================
   ARTICLE IMAGE
========================================================= */

function ArticleImage({
  src,
  alt,
}: {
  src: string | null;
  alt: string;
}) {
  const imageUrl =
    typeof src === "string" && src.trim()
      ? src.trim()
      : null;

  if (!imageUrl) {
    return (
      <div className="flex aspect-[16/9] w-full items-center justify-center overflow-hidden rounded-lg bg-gray-100">
        <span className="px-4 text-center font-serif text-lg text-gray-400">
          Nepal News & Magazine
        </span>
      </div>
    );
  }

  return (
    <div className="relative aspect-[16/9] w-full overflow-hidden rounded-lg bg-gray-100">
      <img
        src={imageUrl}
        alt={alt}
        loading="lazy"
        decoding="async"
        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
        onError={(event) => {
          const image = event.currentTarget;

          /*
           * Prevent infinite error loops.
           */
          image.onerror = null;

          image.style.display = "none";

          const fallback =
            image.parentElement?.querySelector(
              "[data-image-fallback]"
            );

          if (fallback instanceof HTMLElement) {
            fallback.style.display = "flex";
          }
        }}
      />

      <div
        data-image-fallback
        className="absolute inset-0 hidden items-center justify-center bg-gray-100"
      >
        <span className="px-4 text-center font-serif text-lg text-gray-400">
          Nepal News & Magazine
        </span>
      </div>
    </div>
  );
}

/* =========================================================
   DATE FORMATTER
========================================================= */

function formatDate(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}
