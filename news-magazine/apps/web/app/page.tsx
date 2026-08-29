"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@supabase/supabase-js";

// =============================================================
// CONFIG
// =============================================================

// Requires NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to be
// set in your Vercel project (client-side code can only use NEXT_PUBLIC_*
// env vars — never put a service-role key here).
const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL as string,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY as string
);

const CATEGORIES = [
  { label: "National", slug: "national" },
  { label: "Politics", slug: "politics" },
  { label: "Business", slug: "business" },
  { label: "Technology", slug: "technology" },
  { label: "Sports", slug: "sports" },
  { label: "Entertainment", slug: "entertainment" },
];

const PAGE_SIZE = 12;
const FETCH_LIMIT = 300;
const AUTO_REFRESH_MS = 60_000; // 1 minute
const FALLBACK_IMAGE =
  "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80";

type Post = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  video_url?: string | null;
  audio_url?: string | null;
  gif_url?: string | null;
  published_at: string | null;
  sector?: string;
};

// =============================================================
// ICONS
// =============================================================

function LogoIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" className="shrink-0" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M6 8h6M6 11h6M6 14h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <rect x="14" y="8" width="4" height="4" rx="0.5" stroke="currentColor" strokeWidth="1.4" />
      <path d="M14 15h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function ReloadIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      className={spinning ? "animate-spin" : ""}
      aria-hidden="true"
    >
      <path d="M20 11a8 8 0 1 0-2.34 5.66" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M20 5v6h-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// =============================================================
// NAVBAR
// =============================================================

function Navbar({ onReload, reloading }: { onReload: () => void; reloading: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4">
        <Link
          href="/"
          className="flex items-center gap-2 font-bold text-gray-900 transition hover:text-blue-600 sm:text-xl"
        >
          <LogoIcon />
          <span className="truncate">Nepal News &amp; Magazine</span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {CATEGORIES.map((cat) => (
            <Link
              key={cat.slug}
              href={`/category/${cat.slug}`}
              className="rounded-md px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100 hover:text-black"
            >
              {cat.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onReload}
            aria-label="Reload latest news"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-gray-300 text-gray-700 transition hover:border-black hover:bg-gray-900 hover:text-white active:scale-95"
          >
            <ReloadIcon spinning={reloading} />
          </button>

          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 text-gray-700 transition hover:bg-gray-100 md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              {menuOpen ? (
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              ) : (
                <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {menuOpen && (
        <nav className="flex flex-col gap-1 border-t border-gray-200 bg-white px-4 py-3 md:hidden">
          {CATEGORIES.map((cat) => (
            <Link
              key={cat.slug}
              href={`/category/${cat.slug}`}
              onClick={() => setMenuOpen(false)}
              className="rounded-md px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
            >
              {cat.label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}

// =============================================================
// NEWS CARD
// =============================================================

function formatDate(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function NewsCard({ article }: { article: Post }) {
  const imageSrc = article.gif_url || article.featured_image_url || FALLBACK_IMAGE;

  return (
    <Link
      href={`/article/${article.slug}`}
      className="group flex flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm transition duration-200 hover:-translate-y-1 hover:border-gray-300 hover:shadow-lg"
    >
      <div className="relative aspect-video w-full overflow-hidden bg-gray-100">
        {article.video_url ? (
          <video
            src={article.video_url}
            poster={article.featured_image_url || undefined}
            className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
            muted
            loop
            playsInline
            controls
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imageSrc}
            alt={article.headline}
            loading="lazy"
            className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
            onError={(e) => {
              const target = e.currentTarget;
              if (target.src !== FALLBACK_IMAGE) target.src = FALLBACK_IMAGE;
            }}
          />
        )}

        {article.sector && (
          <span className="absolute left-2 top-2 rounded bg-black/70 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
            {article.sector}
          </span>
        )}
      </div>

      {article.audio_url && (
        <audio
          src={article.audio_url}
          controls
          className="mt-2 w-full px-3"
          onClick={(e) => e.stopPropagation()}
        />
      )}

      <div className="flex flex-1 flex-col gap-2 p-4">
        <h2 className="text-base font-bold leading-snug text-gray-900 transition group-hover:text-blue-600 sm:text-lg">
          {article.headline}
        </h2>
        {article.excerpt && <p className="line-clamp-3 text-sm text-gray-600">{article.excerpt}</p>}
        <span className="mt-auto pt-2 text-xs text-gray-400">{formatDate(article.published_at)}</span>
      </div>
    </Link>
  );
}

// =============================================================
// EMPTY STATE
// =============================================================

function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 py-20 text-center">
      <h2 className="text-lg font-semibold text-gray-800">{title}</h2>
      {description && <p className="max-w-sm text-sm text-gray-500">{description}</p>}
    </div>
  );
}

// =============================================================
// PAGINATION
// =============================================================

function Pagination({
  currentPage,
  totalPages,
  onPageChange,
}: {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  const hasPrev = currentPage > 1;
  const hasNext = currentPage < totalPages;

  return (
    <div className="mt-10 flex items-center justify-center gap-3">
      <button
        type="button"
        disabled={!hasPrev}
        onClick={() => onPageChange(currentPage - 1)}
        className={`rounded-md border px-4 py-2 text-sm font-medium transition ${
          hasPrev
            ? "border-gray-300 text-gray-700 hover:border-black hover:bg-gray-900 hover:text-white"
            : "cursor-not-allowed border-gray-200 text-gray-300"
        }`}
      >
        ← Previous
      </button>

      <span className="text-sm text-gray-600">
        Page <span className="font-semibold text-gray-900">{currentPage}</span> of{" "}
        <span className="font-semibold text-gray-900">{totalPages}</span>
      </span>

      <button
        type="button"
        disabled={!hasNext}
        onClick={() => onPageChange(currentPage + 1)}
        className={`rounded-md border px-4 py-2 text-sm font-medium transition ${
          hasNext
            ? "border-gray-300 text-gray-700 hover:border-black hover:bg-gray-900 hover:text-white"
            : "cursor-not-allowed border-gray-200 text-gray-300"
        }`}
      >
        Next →
      </button>
    </div>
  );
}

// =============================================================
// DATA FETCH
// =============================================================

async function fetchPublications(): Promise<Post[]> {
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

  const formattedPosts: Post[] = (postsData ?? []).map((item: any) => ({
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

  const formattedArticles: Post[] = (articlesData ?? []).map((item: any) => ({
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
  const uniqueMap = new Map<string, Post>();
  for (const item of combined) {
    if (item.slug && !uniqueMap.has(item.slug)) uniqueMap.set(item.slug, item);
  }

  return Array.from(uniqueMap.values()).sort((a, b) => {
    const aTime = a.published_at ? new Date(a.published_at).getTime() : 0;
    const bTime = b.published_at ? new Date(b.published_at).getTime() : 0;
    return bTime - aTime;
  });
}

// =============================================================
// PAGE
// =============================================================

export default function HomePage() {
  const router = useRouter();
  const [publications, setPublications] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async () => {
    setReloading(true);
    try {
      const data = await fetchPublications();
      setPublications(data);
    } catch (err) {
      console.error("[HomePage] Failed to load publications:", err);
    } finally {
      setLoading(false);
      setTimeout(() => setReloading(false), 500);
    }
  };

  useEffect(() => {
    load();
    intervalRef.current = setInterval(load, AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return publications;
    return publications.filter(
      (item) =>
        item.headline.toLowerCase().includes(q) || (item.excerpt || "").toLowerCase().includes(q)
    );
  }, [publications, query]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <>
      <Navbar onReload={load} reloading={reloading} />

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

            <div className="flex items-center gap-2">
              <input
                type="search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
                placeholder="Search news..."
                className="h-10 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-black focus:ring-1 focus:ring-black sm:w-64"
              />
            </div>
          </div>

          {loading ? (
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                  <div className="aspect-video w-full animate-pulse bg-gray-200" />
                  <div className="space-y-2 p-4">
                    <div className="h-4 w-5/6 animate-pulse rounded bg-gray-200" />
                    <div className="h-4 w-2/3 animate-pulse rounded bg-gray-200" />
                  </div>
                </div>
              ))}
            </div>
          ) : pageItems.length === 0 ? (
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

          <Pagination currentPage={safePage} totalPages={totalPages} onPageChange={setPage} />
        </main>
    </>
  );
}
