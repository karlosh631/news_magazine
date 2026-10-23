import type { Metadata } from "next";

import { EmptyState } from "@/components/EmptyState";
import { ImagelessGrid } from "@/components/ImagelessGrid";
import { ResurrectionNotice } from "@/components/ResurrectionNotice";
import { searchArticles, toCardArticle } from "@/lib/news";
import { triggerResurrection } from "@/lib/resurrection";
import { SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";

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
 *
 * GOOGLEBOT IS TOLD SEPARATELY
 * -----------------------------
 * The layout declares a robots directive for Google as well as for
 * everyone else, and a page's own robots directive REPLACES the
 * layout's rather than merging with it — so on this page the
 * `googlebot` tag was not inherited, and only the general one was
 * left saying noindex. That happens to be the right answer, but it
 * was right by accident. Both are stated here so that the page
 * cannot become indexable for Google by a change in the layout.
 *
 * THE CANONICAL
 * -------------
 * The layout's canonical is the homepage's address, and this page
 * inherited it: every query string claimed the homepage as its
 * canonical URL. A canonical link says "the real version of this
 * page is over there", and for a noindex page that is a
 * contradiction — the page asks not to be indexed and then asks to
 * be indexed AS the homepage. It points at itself instead.
 */
export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}): Promise<Metadata> {
  const { q } = (await searchParams) ?? {};
  const hasQuery = Boolean(q && q.trim().length > 0);

  // THE EMPTY SEARCH PAGE IS A DIFFERENT PAGE
  // -----------------------------------------
  // `/search` on its own is a real page: it has a heading, it explains
  // what the site searches, and it is the way in to every story. It is
  // indexable, and it says so. `/search?q=...` is a RESULT, not a page,
  // and results are not indexed - so the directive is decided by
  // whether there is a query.
  return {
    title: hasQuery ? `Search: ${q}` : "Search",
    alternates: { canonical: "/search" },
    robots: hasQuery
      ? {
          index: false,
          follow: true,
          googleBot: { index: false, follow: true },
        }
      : {
          index: true,
          follow: true,
          googleBot: { index: true, follow: true },
        },
  };
}

const PAGE_SIZE = 12;

type SearchParams = Promise<{ q?: string; page?: string }>;

/**
 * What this page IS, for a machine.
 *
 * A search results page is its own type, and saying so is what tells
 * a crawler that the list under the search box is the answer to a
 * question rather than a page of ordinary stories. The site already
 * describes itself this way on the homepage and on the category
 * pages; this page was the one that said nothing.
 *
 * The SearchAction below is not decoration: it names the search box
 * that produced the results, and its target is this site's own
 * search - `/search?q=`, which is what the header's search box and
 * the search page itself use.
 *
 * jsonLd() escapes `<`, so a query containing `</script>` cannot
 * close this element early and inject markup.
 */
function SearchStructuredData({ name }: { name: string }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{
        __html: jsonLd({
          "@context": "https://schema.org",
          "@type": "SearchResultsPage",
          name,
          url: `${SITE_URL}/search`,
          isPartOf: {
            "@type": "WebSite",
            name: SITE_NAME,
            url: SITE_URL,
          },
          potentialAction: {
            "@type": "SearchAction",
            target: {
              "@type": "EntryPoint",
              urlTemplate: `${SITE_URL}/search?q={search_term_string}`,
            },
            "query-input": "required name=search_term_string",
          },
        }),
      }}
    />
  );
}

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
        <SearchStructuredData name="Search news" />
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
        <SearchStructuredData name={`Results for “${query}”`} />
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
      <SearchStructuredData name={`Results for “${query}”`} />
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
          {/* As on the category pages: this passes onImageUnavailable, so
              a result whose image 404s is replaced rather than left
              showing "No image available". */}
          <ImagelessGrid articles={pageItems} />

          {totalPages > 1 && (
            <nav className="mt-10 flex items-center justify-center gap-2" aria-label="Pagination">
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                <a
                  key={page}
                  href={`/search?q=${encodeURIComponent(query)}&page=${page}`}
                  aria-current={page === safePage ? "page" : undefined}
                  /*
                    48 pixels tall.

                    A page number is a number in a box, and the box
                    was as tall as the number - 32 pixels, with the
                    next box eight pixels away. A finger going for
                    page 3 could land on page 2 instead. The padding
                    is what turns a row of digits into a row of
                    buttons.
                  */
                  className={`inline-flex min-h-[48px] min-w-[48px] items-center justify-center rounded px-3 text-sm font-medium transition ${
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