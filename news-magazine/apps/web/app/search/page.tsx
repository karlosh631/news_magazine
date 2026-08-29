import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NewsCard, type NewsCardArticle } from "@/components/NewsCard";
import { EmptyState } from "@/components/EmptyState";
import { Pagination } from "@/components/Pagination";

export const revalidate = 0;

const PAGE_SIZE = 12;
const FETCH_LIMIT = 300;

type HomePageProps = {
  searchParams: Promise<{ q?: string; page?: string }>;
};

async function getPublications(): Promise<NewsCardArticle[]> {
  const supabase = createServerSupabaseClient();

  const { data: postsData, error: postsError } = await supabase
    .from("posts")
    .select("*")
    .order("published_at", { ascending: false })
    .limit(FETCH_LIMIT);
  if (postsError) console.error("[Database] 'posts' query error:", postsError.message);

  const { data: articlesData, error: articlesError } = await supabase
    .from("articles")
    .select("*")
    .order("published_at", { ascending: false })
    .limit(FETCH_LIMIT);
  if (articlesError) console.error("[Database] 'articles' query error:", articlesError.message);

  const formattedPosts: NewsCardArticle[] = (postsData ?? []).map((item) => ({
    id: item.id || item.slug || String(Math.random()),
    slug: item.slug || item.id,
    headline: item.headline || item.title || "Untitled Article",
    excerpt: item.excerpt || item.abstract || item.content_ieee?.slice(0, 160) || null,
    featured_image_url: item.featured_image_url || item.cover_image_url || null,
    video_url: item.video_url || null,
    audio_url: item.audio_url || null,
    gif_url: item.gif_url || null,
    published_at: item.published_at || null,
    sector: item.sector || "General",
  }));

  const formattedArticles: NewsCardArticle[] = (articlesData ?? []).map((item) => ({
    id: item.id || item.slug || String(Math.random()),
    slug: item.slug || item.id,
    headline: item.headline || item.title || "Untitled Article",
    excerpt: item.excerpt || item.abstract || null,
    featured_image_url: item.featured_image_url || item.cover_image_url || null,
    video_url: item.video_url || null,
    audio_url: item.audio_url || null,
    gif_url: item.gif_url || null,
    published_at: item.published_at || null,
    sector: item.category || "General",
  }));

  const combined = [...formattedPosts, ...formattedArticles];
  const uniqueMap = new Map<string, NewsCardArticle>();
  for (const item of combined) {
    if (item.slug && !uniqueMap.has(item.slug)) uniqueMap.set(item.slug, item);
  }

  return Array.from(uniqueMap.values()).sort((a, b) => {
    const aTime = a.published_at ? new Date(a.published_at).getTime() : 0;
    const bTime = b.published_at ? new Date(b.published_at).getTime() : 0;
    return bTime - aTime;
  });
}

export default async function HomePage({ searchParams }: HomePageProps) {
  const params = await searchParams;
  const query = (params.q || "").trim().toLowerCase();
  const currentPage = Math.max(1, parseInt(params.page || "1", 10) || 1);

  let publications: NewsCardArticle[] = [];
  try {
    publications = await getPublications();
  } catch (err) {
    console.error("[HomePage] Failed to load publications:", err);
  }

  const filtered = query
    ? publications.filter(
        (item) =>
          item.headline.toLowerCase().includes(query) ||
          (item.excerpt || "").toLowerCase().includes(query)
      )
    : publications;

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(currentPage, totalPages);
  const pageItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <div className="mb-8 flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            UPDATING ENGINE
          </span>
          <h1 className="text-2xl font-bold text-gray-900 mt-1 sm:text-3xl">
            Daily Publications &amp; News
          </h1>
          <p className="mt-1 text-sm text-gray-600">Real-time news stream across Nepal &amp; World.</p>
        </div>

        <form action="/" method="GET" className="flex items-center gap-2">
          <input
            type="search"
            name="q"
            defaultValue={params.q || ""}
            placeholder="Search news..."
            className="h-10 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-black focus:ring-1 focus:ring-black sm:w-64"
          />
          <button
            type="submit"
            className="h-10 shrink-0 rounded-md bg-black px-4 text-sm font-medium text-white transition hover:bg-gray-800"
          >
            Search
          </button>
        </form>
      </div>

      {pageItems.length === 0 ? (
        <EmptyState
          title="No publications found"
          description="Try a different search term, or run your ingestion worker to import the latest news."
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {pageItems.map((item) => (
            <NewsCard key={item.id} article={item} />
          ))}
        </div>
      )}

      <Pagination basePath="/" currentPage={safePage} totalPages={totalPages} searchParams={{ q: params.q }} />
    </main>
  );
}
