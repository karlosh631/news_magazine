"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@supabase/supabase-js";

// =============================================================
// CONFIG
// =============================================================

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
// ICONS & LOGO
// =============================================================

function BrandLogo({ spinning }: { spinning: boolean }) {
  return (
    <div className="relative shrink-0">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 64 64"
        className={`h-9 w-9 sm:h-10 sm:w-10 transform transition-all duration-300 ease-out 
          group-hover:-translate-y-1 group-hover:scale-105 group-hover:rotate-2 group-hover:drop-shadow-[0_4px_14px_rgba(6,182,212,0.45)] 
          active:translate-y-0 active:scale-95 ${spinning ? "animate-spin" : ""}`}
      >
        <defs>
          <linearGradient id="nav-bg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#111827" />
            <stop offset="55%" stopColor="#1d4ed8" />
            <stop offset="100%" stopColor="#06b6d4" />
          </linearGradient>

          <linearGradient id="nav-accent" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#dbeafe" />
          </linearGradient>

          <filter id="nav-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="2" stdDeviation="2" floodOpacity="0.28" />
          </filter>
        </defs>

        {/* Background Card */}
        <rect width="64" height="64" rx="16" fill="url(#nav-bg)" />

        {/* Globe Overlay */}
        <circle cx="32" cy="32" r="22" fill="none" stroke="#ffffff" strokeOpacity="0.12" strokeWidth="1.5" />
        <ellipse cx="32" cy="32" rx="10" ry="22" fill="none" stroke="#ffffff" strokeOpacity="0.10" strokeWidth="1.5" />
        <path d="M10 32h44" fill="none" stroke="#ffffff" strokeOpacity="0.10" strokeWidth="1.5" />

        {/* N24 Monogram */}
        <g filter="url(#nav-shadow)">
          <path d="M14 44V20h5l10 15V20h6v24h-5L20 29v15z" fill="url(#nav-accent)" />
          <path
            d="M39 25 c0-3 2-5 6-5 c4 0 6 2 6 5 c0 3-2 5-5 7 l-4 3h9v5H35v-4 l10-8 c1-1 2-2 2-3 c0-1-1-2-2-2 c-1 0-2 1-2 3z"
            fill="url(#nav-accent)"
          />
        </g>

        {/* Breaking-News Pulse Indicator */}
        <circle cx="51" cy="13" r="5" fill="#ef4444" />
        <circle cx="51" cy="13" r="2" fill="#ffffff" />

        {/* Signal Lines */}
        <path d="M43 10h5" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" opacity="0.9" />
      </svg>

      {/* Live Badge Signal Ring */}
      <span className="absolute -top-1 -right-1 flex h-3.5 w-3.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75"></span>
        <span className="relative inline-flex h-3.5 w-3.5 rounded-full bg-red-500"></span>
      </span>
    </div>
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
        
        {/* Click-to-Reload Brand Logo & Title */}
        <button
          type="button"
          onClick={onReload}
          title="Click to reload latest news stream"
          aria-label="Reload homepage feed"
          className="group flex items-center gap-3 border-0 bg-transparent text-left outline-none cursor-pointer focus:outline-none"
        >
          <BrandLogo spinning={reloading} />
          <span className="font-bold text-gray-900 transition-colors group-hover:text-blue-600 sm:text-xl truncate">
            Nepal News &amp; Magazine
          </span>
        </button>

        {/* Desktop Category Navigation */}
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

        {/* Actions & Mobile Menu Toggle */}
        <div className="flex items-center gap-2">
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

      {/* Mobile Drawer Menu */}
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
