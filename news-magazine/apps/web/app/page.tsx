import Link from "next/link";
import { createServerSupabaseClient } from "@/lib/supabase/server";

import { NewsCard } from "@/components/NewsCard";
import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { EmptyState } from "@/components/EmptyState";

/*
 * Revalidate the homepage every 60 seconds.
 *
 * This means newly ingested articles can appear automatically
 * without manually rebuilding/deploying the website.
 */
export const revalidate = 60;

/* =========================================================
   TYPES
   ========================================================= */

type Category = {
  id: string;
  slug: string;
  name: string;
};

type Article = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  published_at: string | null;
  primary_category_id: string | null;
};

type BreakingArticle = {
  id: string;
  slug: string;
  headline: string;
  featured_image_url: string | null;
  published_at: string | null;
};

/* =========================================================
   CATEGORY DISPLAY ORDER
   ========================================================= */

const CATEGORY_ORDER = [
  "national",
  "politics",
  "business",
  "technology",
  "sports",
  "entertainment",
];

/* =========================================================
   GET HOMEPAGE DATA
   ========================================================= */

async function getHomepageData() {
  const supabase = createServerSupabaseClient();

  /*
   * Fetch categories first.
   *
   * We intentionally do this separately instead of relying on
   * Supabase foreign-table relationships. This makes the
   * application work even if the database relationship is not
   * configured for nested selects.
   */
  const {
    data: categories,
    error: categoriesError,
  } = await supabase
    .from("categories")
    .select("id, slug, name");

  if (categoriesError) {
    console.error(
      "[Homepage] Failed to load categories:",
      categoriesError
    );
  }

  const allCategories: Category[] = categories ?? [];

  /*
   * Fetch breaking and latest news simultaneously.
   */
  const [
    breakingResponse,
    latestResponse,
  ] = await Promise.all([
    supabase
      .from("articles")
      .select(
        "id, slug, headline, featured_image_url, published_at"
      )
      .eq("status", "published")
      .eq("is_breaking", true)
      .order("published_at", {
        ascending: false,
        nullsFirst: false,
      })
      .limit(8),

    supabase
      .from("articles")
      .select(
        [
          "id",
          "slug",
          "headline",
          "excerpt",
          "featured_image_url",
          "published_at",
          "primary_category_id",
        ].join(", ")
      )
      .eq("status", "published")
      .order("published_at", {
        ascending: false,
        nullsFirst: false,
      })
      .limit(50),
  ]);

  if (breakingResponse.error) {
    console.error(
      "[Homepage] Failed to load breaking news:",
      breakingResponse.error
    );
  }

  if (latestResponse.error) {
    console.error(
      "[Homepage] Failed to load latest news:",
      latestResponse.error
    );
  }

  const breaking: BreakingArticle[] =
    breakingResponse.data ?? [];

  const latest: Article[] =
    latestResponse.data ?? [];

  /*
   * Organize latest articles into their categories.
   *
   * We use primary_category_id rather than category slug
   * because articles store the category UUID.
   */
  const categorySections = allCategories
    .filter((category) =>
      CATEGORY_ORDER.includes(category.slug)
    )
    .sort(
      (a, b) =>
        CATEGORY_ORDER.indexOf(a.slug) -
        CATEGORY_ORDER.indexOf(b.slug)
    )
    .map((category) => ({
      category,
      articles: latest
        .filter(
          (article) =>
            article.primary_category_id === category.id
        )
        .slice(0, 6),
    }));

  return {
    breaking,
    latest,
    categorySections,
  };
}

/* =========================================================
   CATEGORY SECTION
   ========================================================= */

function CategorySection({
  category,
  articles,
}: {
  category: Category;
  articles: Article[];
}) {
  /*
   * Don't display an empty category section.
   */
  if (articles.length === 0) {
    return null;
  }

  return (
    <section
      className="mt-12"
      aria-labelledby={`category-${category.slug}`}
    >
      {/* ---------------------------------------------------
          CATEGORY HEADER
          --------------------------------------------------- */}

      <div className="mb-5 flex items-center justify-between border-b pb-3">
        <h2
          id={`category-${category.slug}`}
          className="font-headline text-2xl font-bold"
        >
          {category.name}
        </h2>

        <Link
          href={`/category/${category.slug}`}
          className="text-sm font-medium hover:underline"
        >
          View all →
        </Link>
      </div>

      {/* ---------------------------------------------------
          CATEGORY ARTICLES
          --------------------------------------------------- */}

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {articles.map((article) => (
          <NewsCard
            key={article.id}
            article={article}
          />
        ))}
      </div>
    </section>
  );
}

/* =========================================================
   HOMEPAGE
   ========================================================= */

export default async function HomePage() {
  let homepage;

  /*
   * Prevent the entire homepage from crashing because of
   * an unexpected Supabase/network error.
   */
  try {
    homepage = await getHomepageData();
  } catch (error) {
    console.error(
      "[Homepage] Unexpected error:",
      error
    );

    return (
      <main className="mx-auto max-w-6xl px-4 py-16">
        <div className="mx-auto max-w-xl text-center">
          <h1 className="font-headline text-3xl font-bold">
            Unable to load news
          </h1>

          <p className="mt-3 text-gray-600">
            We couldn't load the latest news right now.
            Please try again shortly.
          </p>

          <Link
            href="/"
            className="mt-6 inline-block rounded-md border px-5 py-2 text-sm font-medium hover:bg-gray-50"
          >
            Refresh
          </Link>
        </div>
      </main>
    );
  }

  const {
    breaking,
    latest,
    categorySections,
  } = homepage;

  return (
    <>
      {/* ===================================================
          BREAKING NEWS
          =================================================== */}

      {breaking.length > 0 && (
        <BreakingNewsTicker items={breaking} />
      )}

      {/* ===================================================
          MAIN CONTENT
          =================================================== */}

      <main className="mx-auto max-w-6xl px-4 py-8">

        {/* =================================================
            LATEST NEWS HEADER
            ================================================= */}

        <section aria-labelledby="latest-news-heading">
          <div className="mb-5 flex items-center justify-between border-b pb-3">
            <h1
              id="latest-news-heading"
              className="font-headline text-2xl font-bold"
            >
              ताजा समाचार · Latest News
            </h1>

            <Link
              href="/search"
              className="text-sm font-medium hover:underline"
            >
              Search →
            </Link>
          </div>

          {/* -----------------------------------------------
              NO ARTICLES
              ----------------------------------------------- */}

          {latest.length === 0 ? (
            <EmptyState
              title="No articles yet"
              description="Once sources are enabled and the ingestion service runs its first sync, articles will appear here automatically."
            />
          ) : (
            /* ---------------------------------------------
               LATEST NEWS GRID
               --------------------------------------------- */

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {latest
                .slice(0, 12)
                .map((article) => (
                  <NewsCard
                    key={article.id}
                    article={article}
                  />
                ))}
            </div>
          )}
        </section>

        {/* =================================================
            CATEGORY SECTIONS
            ================================================= */}

        {categorySections.map(
          ({
            category,
            articles,
          }) => (
            <CategorySection
              key={category.id}
              category={category}
              articles={articles}
            />
          )
        )}

        {/* =================================================
            VIEW ALL NEWS
            ================================================= */}

        {latest.length > 12 && (
          <div className="mt-10 text-center">
            <Link
              href="/search"
              className="inline-flex rounded-md border px-6 py-3 text-sm font-medium transition hover:bg-gray-50"
            >
              View More News
            </Link>
          </div>
        )}

      </main>
    </>
  );
}
