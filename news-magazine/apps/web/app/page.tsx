import { createServerSupabaseClient } from "@/lib/supabase/server";

import { NewsCard } from "@/components/NewsCard";
import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { EmptyState } from "@/components/EmptyState";

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
   CATEGORY ORDER
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

  /* -------------------------------------------------------
     GET CATEGORIES
     ------------------------------------------------------- */

  const { data: categories, error: categoryError } =
    await supabase
      .from("categories")
      .select("id, slug, name");

  if (categoryError) {
    console.error(
      "Homepage category query failed:",
      categoryError
    );
  }

  const allCategories: Category[] = categories ?? [];

  /* -------------------------------------------------------
     GET BREAKING + LATEST IN PARALLEL
     ------------------------------------------------------- */

  const [
    { data: breaking, error: breakingError },
    { data: latest, error: latestError },
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
        "id, slug, headline, excerpt, featured_image_url, published_at, primary_category_id"
      )
      .eq("status", "published")
      .order("published_at", {
        ascending: false,
        nullsFirst: false,
      })
      .limit(30),
  ]);

  if (breakingError) {
    console.error(
      "Homepage breaking-news query failed:",
      breakingError
    );
  }

  if (latestError) {
    console.error(
      "Homepage latest-news query failed:",
      latestError
    );
  }

  /* -------------------------------------------------------
     ORGANIZE ARTICLES BY CATEGORY
     ------------------------------------------------------- */

  const latestArticles: Article[] = latest ?? [];

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
      articles: latestArticles
        .filter(
          (article) =>
            article.primary_category_id === category.id
        )
        .slice(0, 6),
    }));

  return {
    breaking: (breaking ?? []) as BreakingArticle[],
    latest: latestArticles,
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
  if (articles.length === 0) {
    return null;
  }

  return (
    <section className="mt-12">
      {/* ---------------------------------------------------
          SECTION HEADER
          --------------------------------------------------- */}

      <div className="mb-5 flex items-center justify-between border-b pb-3">
        <div>
          <h2 className="font-headline text-2xl font-bold">
            {category.name}
          </h2>
        </div>

        <a
          href={`/category/${category.slug}`}
          className="text-sm font-medium hover:underline"
        >
          View all →
        </a>
      </div>

      {/* ---------------------------------------------------
          ARTICLES
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
  const {
    breaking,
    latest,
    categorySections,
  } = await getHomepageData();

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
            LATEST NEWS
            ================================================= */}

        <section>
          <div className="mb-5 flex items-center justify-between border-b pb-3">
            <h1 className="font-headline text-2xl font-bold">
              ताजा समाचार · Latest News
            </h1>

            <a
              href="/search"
              className="text-sm font-medium hover:underline"
            >
              Search →
            </a>
          </div>

          {latest.length === 0 ? (
            <EmptyState
              title="No articles yet"
              description="Once sources are enabled and the ingestion service runs its first sync, articles will appear here automatically."
            />
          ) : (
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {latest.slice(0, 12).map((article) => (
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
          ({ category, articles }) => (
            <CategorySection
              key={category.id}
              category={category}
              articles={articles}
            />
          )
        )}

      </main>
    </>
  );
}
