import Link from "next/link";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NewsCard } from "@/components/NewsCard";
import { EmptyState } from "@/components/EmptyState";

export const revalidate = 60;

type SearchArticle = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  published_at: string | null;
};

type SearchPageProps = {
  searchParams: Promise<{
    q?: string;
  }>;
};

async function searchArticles(query: string) {
  const supabase = createServerSupabaseClient();

  const cleanQuery = query.trim();

  if (!cleanQuery) {
    return [];
  }

  /*
   * Search published articles only.
   *
   * headline.ilike searches the headline.
   * excerpt.ilike searches the excerpt.
   */
  const pattern = `%${cleanQuery}%`;

  const { data, error } = await supabase
    .from("articles")
    .select(
      [
        "id",
        "slug",
        "headline",
        "excerpt",
        "featured_image_url",
        "published_at",
      ].join(", ")
    )
    .eq("status", "published")
    .or(
      `headline.ilike.${pattern},excerpt.ilike.${pattern}`
    )
    .order("published_at", {
      ascending: false,
      nullsFirst: false,
    })
    .limit(50);

  if (error) {
    console.error(
      "[Search] Failed to search articles:",
      error
    );

    return [];
  }

  return (data ?? []) as SearchArticle[];
}

export default async function SearchPage({
  searchParams,
}: SearchPageProps) {
  const params = await searchParams;
  const query = params.q?.trim() ?? "";

  const results = await searchArticles(query);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      {/* =================================================
          PAGE HEADER
          ================================================= */}

      <div className="mb-8">
        <h1 className="font-headline text-3xl font-bold">
          Search News
        </h1>

        <p className="mt-2 text-sm text-gray-600">
          Search published news by headline or excerpt.
        </p>
      </div>

      {/* =================================================
          SEARCH FORM
          ================================================= */}

      <form
        action="/search"
        method="GET"
        className="mb-10"
      >
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="search"
            name="q"
            defaultValue={query}
            placeholder="Search news..."
            aria-label="Search news"
            className="min-h-12 flex-1 rounded-md border border-gray-300 px-4 text-base outline-none transition focus:border-gray-500 focus:ring-2 focus:ring-gray-200"
          />

          <button
            type="submit"
            className="min-h-12 rounded-md bg-black px-6 font-medium text-white transition hover:opacity-90"
          >
            Search
          </button>
        </div>
      </form>

      {/* =================================================
          NO SEARCH QUERY
          ================================================= */}

      {!query && (
        <EmptyState
          title="Search for news"
          description="Enter a keyword above to search the latest published articles."
        />
      )}

      {/* =================================================
          SEARCH RESULTS
          ================================================= */}

      {query && (
        <section aria-labelledby="search-results-heading">
          <div className="mb-5 border-b pb-3">
            <h2
              id="search-results-heading"
              className="font-headline text-2xl font-bold"
            >
              Search results
            </h2>

            <p className="mt-1 text-sm text-gray-600">
              Results for{" "}
              <span className="font-semibold">
                “{query}”
              </span>
            </p>
          </div>

          {results.length === 0 ? (
            <EmptyState
              title="No articles found"
              description={`We couldn't find any published articles matching “${query}”. Try another keyword.`}
            />
          ) : (
            <>
              <p className="mb-5 text-sm text-gray-500">
                {results.length}{" "}
                {results.length === 1
                  ? "article"
                  : "articles"}{" "}
                found
              </p>

              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {results.map((article) => (
                  <NewsCard
                    key={article.id}
                    article={article}
                  />
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {/* =================================================
          BACK TO HOME
          ================================================= */}

      <div className="mt-12 border-t pt-6">
        <Link
          href="/"
          className="text-sm font-medium hover:underline"
        >
          ← Back to Latest News
        </Link>
      </div>
    </main>
  );
}
