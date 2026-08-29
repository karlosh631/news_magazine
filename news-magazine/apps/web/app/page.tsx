import Link from "next/link";
import Image from "next/image";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/* =========================================================
   DYNAMIC ROUTING & NO-CACHE CONFIG (PER-MINUTE FRESHNESS)
   ========================================================= */

export const dynamic = "force-dynamic";
export const revalidate = 0;

/* =========================================================
   TYPES
   ========================================================= */

export type IEEEReference = {
  id: number;
  citation: string;
  url?: string;
};

export type Post = {
  id: string;
  slug: string;
  title: string;
  abstract: string;
  content_ieee: string;
  sector: "Coding" | "Hackathons" | "Nepal Top News" | "World News" | "IT";
  references_json: IEEEReference[];
  cover_image_url: string | null;
  media_type?: "image" | "video" | "audio";
  media_url?: string | null;
  published_at: string;
  created_at: string;
};

interface PageProps {
  searchParams: Promise<{
    page?: string;
    sector?: string;
    date?: string;
  }>;
}

const ITEMS_PER_PAGE = 9;
const ALL_SECTORS = ["Coding", "Hackathons", "Nepal Top News", "World News", "IT"];

/* =========================================================
   DATABASE QUERY (LIFO + PAGINATION + FILTERS)
   ========================================================= */

async function getPaginatedPosts(page: number, sector?: string, dateFilter?: string) {
  const supabase = createServerSupabaseClient();
  const from = (page - 1) * ITEMS_PER_PAGE;
  const to = from + ITEMS_PER_PAGE - 1;

  let query = supabase
    .from("posts")
    .select("*", { count: "exact" })
    // LIFO Ordering: Latest publications show at the top
    .order("published_at", { ascending: false })
    .range(from, to);

  if (sector && sector !== "ALL") {
    query = query.eq("sector", sector);
  }

  if (dateFilter) {
    // Filter by specific day string (YYYY-MM-DD)
    const startDate = new Date(`${dateFilter}T00:00:00.000Z`).toISOString();
    const endDate = new Date(`${dateFilter}T23:59:59.999Z`).toISOString();
    query = query.gte("published_at", startDate).lte("published_at", endDate);
  }

  const { data, count, error } = await query;

  if (error) {
    console.error("[IEEE Engine] Database fetch error:", error);
  }

  return {
    posts: (data as Post[]) ?? [],
    totalCount: count ?? 0,
    totalPages: Math.ceil((count ?? 0) / ITEMS_PER_PAGE),
  };
}

/* =========================================================
   MULTIMEDIA & ANIMATED POST CARD COMPONENT
   ========================================================= */

function AnimatedPostCard({ post }: { post: Post }) {
  const formattedDate = new Date(post.published_at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <article className="group relative flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-xl hover:border-blue-500/30">
      
      {/* Sector Badge */}
      <div className="absolute top-3 left-3 z-10">
        <span className="inline-flex items-center rounded-full bg-slate-900/80 px-3 py-1 text-xs font-semibold text-white backdrop-blur-md">
          {post.sector}
        </span>
      </div>

      {/* Media Rendering Container (Images, Video, Audio) */}
      <div className="relative h-48 w-full bg-slate-900 overflow-hidden">
        {post.media_type === "video" && post.media_url ? (
          <video
            src={post.media_url}
            controls
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
            poster={post.cover_image_url || undefined}
          />
        ) : post.cover_image_url ? (
          <Image
            src={post.cover_image_url}
            alt={post.title}
            fill
            className="object-cover transition-transform duration-500 group-hover:scale-105"
            sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-slate-800 to-slate-950 text-slate-400">
            <span className="text-sm font-medium">IEEE Technical Brief</span>
          </div>
        )}

        {/* Pulse Live Animation Badge */}
        <div className="absolute bottom-3 right-3 flex items-center space-x-1.5 rounded-full bg-emerald-500/90 px-2.5 py-0.5 text-[10px] font-bold text-white shadow-sm backdrop-blur-sm">
          <span className="h-1.5 w-1.5 animate-ping rounded-full bg-white" />
          <span>LIVE UPDATE</span>
        </div>
      </div>

      {/* Content Container */}
      <div className="flex flex-1 flex-col p-5">
        <div className="mb-2 text-xs font-medium text-slate-500">
          <time dateTime={post.published_at}>{formattedDate}</time>
        </div>

        <h3 className="mb-2 text-lg font-bold leading-snug text-slate-900 transition-colors duration-200 group-hover:text-blue-600 line-clamp-2">
          <Link href={`/article/${post.slug}`}>
            <span className="absolute inset-0" />
            {post.title}
          </Link>
        </h3>

        <p className="mb-4 text-sm leading-relaxed text-slate-600 line-clamp-3">
          {post.abstract}
        </p>

        {/* Audio Embed Player */}
        {post.media_type === "audio" && post.media_url && (
          <div className="mt-auto pt-2 z-20">
            <audio controls src={post.media_url} className="w-full h-8" />
          </div>
        )}

        {/* IEEE Ref Count Footer */}
        <div className="mt-auto flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500">
          <span>
            {post.references_json ? `${post.references_json.length} IEEE Ref(s)` : "Standard IEEE"}
          </span>
          <span className="font-semibold text-blue-600 group-hover:translate-x-1 transition-transform duration-200">
            Read Publication →
          </span>
        </div>
      </div>
    </article>
  );
}

/* =========================================================
   MAIN HOMEPAGE WRAPPER
   ========================================================= */

export default async function HomePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const currentPage = Number(params.page) || 1;
  const currentSector = params.sector || "ALL";
  const currentDate = params.date || "";

  const { posts, totalPages, totalCount } = await getPaginatedPosts(
    currentPage,
    currentSector,
    currentDate
  );

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      
      {/* Header & Live Auto-Update Ticker */}
      <header className="mb-8 border-b border-slate-200 pb-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="relative flex h-3 w-3">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500" />
              </span>
              <span className="text-xs font-semibold uppercase tracking-wider text-emerald-600">
                Auto-Updating Engine Active
              </span>
            </div>
            <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-slate-900 sm:text-4xl">
              IEEE Daily Publications
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              Real-time LIFO IEEE standard news stream across Coding, Hackathons, Nepal & World IT.
            </p>
          </div>

          {/* Quick Refresh Status */}
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-xs font-medium text-white transition hover:bg-slate-800 shadow-sm"
            >
              <svg className="h-3.5 w-3.5 animate-spin text-white" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Auto-Sync
            </Link>
          </div>
        </div>

        {/* Date Filter & Sector Tabs Bar */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-slate-100 pt-4">
          
          {/* Sector Buttons */}
          <div className="flex flex-wrap items-center gap-1.5">
            <Link
              href={`/?sector=ALL${currentDate ? `&date=${currentDate}` : ""}`}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                currentSector === "ALL"
                  ? "bg-blue-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              All Sectors
            </Link>
            {ALL_SECTORS.map((sector) => (
              <Link
                key={sector}
                href={`/?sector=${encodeURIComponent(sector)}${currentDate ? `&date=${currentDate}` : ""}`}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                  currentSector === sector
                    ? "bg-blue-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {sector}
              </Link>
            ))}
          </div>

          {/* Date Picker Filter */}
          <form method="GET" className="flex items-center gap-2">
            {currentSector !== "ALL" && (
              <input type="hidden" name="sector" value={currentSector} />
            )}
            <label htmlFor="date" className="text-xs font-medium text-slate-600">
              Filter by Date:
            </label>
            <input
              type="date"
              id="date"
              name="date"
              defaultValue={currentDate}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-md bg-slate-800 px-3 py-1 text-xs font-medium text-white hover:bg-slate-700"
            >
              Apply
            </button>
            {currentDate && (
              <Link
                href={`/?sector=${currentSector}`}
                className="text-xs text-red-600 hover:underline"
              >
                Clear
              </Link>
            )}
          </form>
        </div>
      </header>

      {/* Publications Grid (LIFO Sequence) */}
      {posts.length === 0 ? (
        <div className="flex min-h-[300px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-slate-50/50 p-8 text-center">
          <p className="text-base font-semibold text-slate-700">No IEEE publications found</p>
          <p className="mt-1 text-xs text-slate-500">
            Try resetting your filters or triggering the daily cron synthesizer at /api/cron/generate-ieee.
          </p>
          <Link
            href="/"
            className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white shadow hover:bg-blue-500"
          >
            Reset Filters
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {posts.map((post) => (
            <AnimatedPostCard key={post.id} post={post} />
          ))}
        </div>
      )}

      {/* Server Side Pagination Navigation */}
      {totalPages > 1 && (
        <nav className="mt-12 flex items-center justify-between border-t border-slate-200 pt-6">
          <div className="text-xs text-slate-500">
            Showing Page <span className="font-semibold text-slate-900">{currentPage}</span> of{" "}
            <span className="font-semibold text-slate-900">{totalPages}</span> ({totalCount} Total Items)
          </div>

          <div className="flex items-center gap-2">
            {currentPage > 1 ? (
              <Link
                href={`/?page=${currentPage - 1}${currentSector !== "ALL" ? `&sector=${currentSector}` : ""}${currentDate ? `&date=${currentDate}` : ""}`}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition"
              >
                ← Previous
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-md border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-300">
                ← Previous
              </span>
            )}

            {currentPage < totalPages ? (
              <Link
                href={`/?page=${currentPage + 1}${currentSector !== "ALL" ? `&sector=${currentSector}` : ""}${currentDate ? `&date=${currentDate}` : ""}`}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition"
              >
                Next →
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-md border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-300">
                Next →
              </span>
            )}
          </div>
        </nav>
      )}
    </main>
  );
}
