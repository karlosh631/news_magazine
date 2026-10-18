import type { Metadata } from "next";

import { EmptyState } from "@/components/EmptyState";
import { NewsCard } from "@/components/NewsCard";
import { ResurrectionNotice } from "@/components/ResurrectionNotice";
import { searchArticles, toCardArticle } from "@/lib/news";
import { triggerResurrection } from "@/lib/resurrection";

export const dynamic = "force-dynamic";

/**
 * Search results must never be indexed.
 *
 * The page had no metadata at all, so it inherited the site defaults —
 * indexable. Every distinct query string then produced a separate
 * indexable URL of thin, duplicate-ish content, which is exactly the
 * pattern that gets a search section deindexed. robots.txt cannot fix
 * this on its own: "allow" in robots.txt only says a page may be
 * crawled, the noindex has to come from a meta robots tag.
 *
 * follow stays true so the links inside still pass authority onward.
 */
export const metadata: Metadata = {
  title: "Search",
  robots: { index: false, follow: true },
};

const PAGE_SIZE = 12;

type SearchParams = Promise<{ q?: string; page?: string }>;

/**
 * Search results page.
 *
 * Stage 1's version named its component `HomePage`, called a function
 * called `getPublications()`, and filtered articles in memory with
 * `.includes()`. It read four columns (`title`, `abstract`,
 * `cover_image_url`, `category`) that do not exist on `articles`, so
 * every value was undefined and the page rendered "Untitled Article"
 * with no image. Its <Pagination> pointed at `basePath="/"`, so paging
 * through results navigated back to the homepage and silently dropped
 * the query.
 *
 * Now it queries search_articles(), and when the retention window has
 * already pruned older stories it calls the Python service's
 * /api/scrape-on-demand to re-fetch them.
 */
export default async function SearchPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const query = (params.q ?? "").trim();
  const currentPage = Math.max(1, parseInt(params.page ?? "1", 10) || 1);

  if (!query) {
    return (
      <main className="mx-auto max-w-7xl px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold text-gray-900">Search news</h1>
        <EmptyState
          title="Search across every outlet"
          description="Type a topic in the search box above. Stories pruned after 48 hours are re-fetched from their original sources automatically."
        />
      </main>
    );
  }

  const result = await searchArticles(query, 60);

  if (result.error) {
    return (
      <main className="mx-auto max-w-7xl px-4 py-8">
        <h1 className="mb-2 text-2xl font-bold text-gray-900">
          Results for “{query}”
        </h1>
        <EmptyState title="Search is unavailable" description={result.error} />
      </main>
    );
  }

  let articles = result.articles;
  let notice: React.ReactNode = null;

  // ---- Resurrection ----
  // If the live table has few matches, the story may have aged out of the
  // 48-hour window. Ask the Python worker to re-poll the sources for it.
  if (articles.length < 3) {
    const resurrected = await triggerResurrection(query);
    if (resurrected) {
      articles = resurrected.articles;
      notice = (
        <ResurrectionNotice
          query={query}
          resurrectedCount={resurrected.resurrectedCount}
          note={resurrected.note}
          rateLimited={resurrected.rateLimited}
        />
      );
    }
  }

  const totalPages = Math.max(1, Math.ceil(articles.length / PAGE_SIZE));
  const safePage = Math.min(currentPage, totalPages);
  const pageItems = articles
    .slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)
    .map(toCardArticle);

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-6 border-b pb-5">
        <span className="text-xs font-semibold uppercase tracking-wider text-blue-600">
          Search
        </span>
        <h1 className="mt-1 text-2xl font-bold text-gray-900 sm:text-3xl">
          Results for “{query}”
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {articles.length} {articles.length === 1 ? "story" : "stories"} across
          every configured outlet.
        </p>
      </header>

      {notice}

      {pageItems.length === 0 ? (
        <EmptyState
          title="No matching stories"
          description={`Nothing published in the last 48 hours matches “${query}”. Try a different spelling, or search in Nepali (e.g. सरकार).`}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {pageItems.map((article) => (
              <NewsCard key={article.id} article={article} />
            ))}
          </div>

          {totalPages > 1 && (
            <nav className="mt-10 flex items-center justify-center gap-2" aria-label="Pagination">
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                <a
                  key={page}
                  href={`/search?q=${encodeURIComponent(query)}&page=${page}`}
                  aria-current={page === safePage ? "page" : undefined}
                  className={`rounded px-3 py-1.5 text-sm font-medium transition ${
                    page === safePage
                      ? "bg-gray-900 text-white"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {page}
                </a>
              ))}
            </nav>
          )}
        </>
      )}
    </main>
  );
}