import Link from "next/link";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NewsCard } from "@/components/NewsCard";
import { EmptyState } from "@/components/EmptyState";

export const revalidate = 0; // Fresh real-time content

type Post = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  published_at: string | null;
  sector?: string;
};

type HomePageProps = {
  searchParams: Promise<{
    sector?: string;
    date?: string;
  }>;
};

async function getPublications(sector?: string, date?: string): Promise<Post[]> {
  const supabase = createServerSupabaseClient();

  // 1. Fetch from 'posts' table with fallback attributes
  let postsQuery = supabase
    .from("posts")
    .select("*")
    .order("published_at", { ascending: false });

  if (sector && sector !== "All Sectors") {
    postsQuery = postsQuery.ilike("sector", `%${sector}%`);
  }

  if (date) {
    postsQuery = postsQuery.gte("published_at", `${date}T00:00:00.000Z`);
  }

  const { data: postsData, error: postsError } = await postsQuery.limit(50);

  if (postsError) {
    console.error("[Database] Query error on 'posts' table:", postsError.message);
  }

  // 2. Fetch fallback from 'articles' table
  let articlesQuery = supabase
    .from("articles")
    .select("*")
    .order("published_at", { ascending: false });

  if (date) {
    articlesQuery = articlesQuery.gte("published_at", `${date}T00:00:00.000Z`);
  }

  const { data: articlesData, error: articlesError } = await articlesQuery.limit(50);

  if (articlesError) {
    console.error("[Database] Query error on 'articles' table:", articlesError.message);
  }

  // Map 'posts' table records dynamically
  const formattedPosts: Post[] = (postsData ?? []).map((item) => ({
    id: item.id || String(Math.random()),
    slug: item.slug || item.id,
    headline: item.headline || item.title || "Untitled Article",
    excerpt: item.excerpt || item.abstract || item.content_ieee?.slice(0, 160) || null,
    featured_image_url: item.featured_image_url || item.cover_image_url || null,
    published_at: item.published_at || new Date().toISOString(),
    sector: item.sector || "General",
  }));

  // Map 'articles' table records dynamically
  const formattedArticles: Post[] = (articlesData ?? []).map((item) => ({
    id: item.id || String(Math.random()),
    slug: item.slug || item.id,
    headline: item.headline || item.title || "Untitled Article",
    excerpt: item.excerpt || item.abstract || null,
    featured_image_url: item.featured_image_url || item.cover_image_url || null,
    published_at: item.published_at || new Date().toISOString(),
    sector: item.category || "General",
  }));

  // Merge, prioritize 'posts', and deduplicate by slug
  const combined = [...formattedPosts, ...formattedArticles];
  const uniqueMap = new Map<string, Post>();

  for (const item of combined) {
    if (item.slug && !uniqueMap.has(item.slug)) {
      uniqueMap.set(item.slug, item);
    }
  }

  return Array.from(uniqueMap.values());
}

export default async function HomePage({ searchParams }: HomePageProps) {
  const params = await searchParams;
  const currentSector = params.sector || "All Sectors";
  const currentDate = params.date || "";

  const publications = await getPublications(
    currentSector === "All Sectors" ? undefined : currentSector,
    currentDate || undefined
  );

  const sectors = [
    "All Sectors",
    "Coding",
    "Hackathons",
    "Nepal Top News",
    "World News",
    "IT",
  ];

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      {/* HEADER WITH SEARCH BAR & CLEANED BADGE */}
      <div className="mb-8 flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            UPDATING ENGINE
          </span>
          <h1 className="font-headline text-3xl font-bold text-gray-900 mt-1">
            Daily Publications & News
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            Real-time news stream across Nepal & World.
          </p>
        </div>

        {/* SEARCH BAR FORM */}
        <form action="/search" method="GET" className="flex items-center gap-2">
          <input
            type="search"
            name="q"
            placeholder="Search news..."
            className="h-10 rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-black focus:ring-1 focus:ring-black"
          />
          <button
            type="submit"
            className="h-10 rounded-md bg-black px-4 text-sm font-medium text-white transition hover:bg-gray-800"
          >
            Search
          </button>
        </form>
      </div>

      {/* FILTER & SECTOR CONTROLS */}
      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-2">
          {sectors.map((sector) => {
            const isActive = currentSector === sector;
            return (
              <Link
                key={sector}
                href={
                  sector === "All Sectors"
                    ? "/"
                    : `/?sector=${encodeURIComponent(sector)}`
                }
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  isActive
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                }`}
              >
                {sector}
              </Link>
            );
          })}
        </div>

        {/* DATE FILTER */}
        <form action="/" method="GET" className="flex items-center gap-2">
          {currentSector !== "All Sectors" && (
            <input type="hidden" name="sector" value={currentSector} />
          )}
          <label htmlFor="date" className="text-xs text-gray-500">
            Filter by Date:
          </label>
          <input
            type="date"
            id="date"
            name="date"
            defaultValue={currentDate}
            className="h-8 rounded border border-gray-300 px-2 text-xs outline-none"
          />
          <button
            type="submit"
            className="h-8 rounded bg-gray-900 px-3 text-xs font-medium text-white hover:bg-gray-800"
          >
            Apply
          </button>
        </form>
      </div>

      {/* PUBLICATIONS DISPLAY GRID */}
      {publications.length === 0 ? (
        <EmptyState
          title="No publications found"
          description="Try resetting your filters or running your ingestion worker to import latest news."
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {publications.map((item) => (
            <NewsCard key={item.id} article={item} />
          ))}
        </div>
      )}
    </main>
  );
}
