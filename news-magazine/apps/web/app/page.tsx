"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { createClient } from "@supabase/supabase-js";
import { Pagination } from "@/components/Pagination";

// =============================================================
// CONFIG
// =============================================================

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL as string,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY as string
);

const PAGE_SIZE = 12;
const FETCH_LIMIT = 300;
const FALLBACK_IMAGE =
  "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80";

type Article = {
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
// HELPERS & CARD
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

function NewsCard({ article }: { article: Article }) {
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

function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 py-20 text-center">
      <h2 className="text-lg font-semibold text-gray-800">{title}</h2>
      {description && <p className="max-w-sm text-sm text-gray-500">{description}</p>}
    </div>
  );
}

// =============================================================
// DATA FETCH
// =============================================================

async function fetchArticles(): Promise<Article[]> {
  const { data, error } = await supabase
    .from("articles")
    .select("*")
    .order("published_at", { ascending: false })
    .limit(FETCH_LIMIT);

  if (error) {
    console.error("[Database] 'articles' query error:", error.message);
    return [];
  }

  return (data ?? []).map((item: any) => ({
    id: item.id || item.slug || String(Math.random()),
    slug: item.slug || item.id,
    headline: item.headline || item.title || "Untitled Article",
    excerpt: item.excerpt || item.abstract || null,
    featured_image_url: item.featured_image_url || item.cover_image_url || item.image_url || null,
    video_url: item.video_url || null,
    audio_url: item.audio_url || null,
    gif_url: item.gif_url || null,
    published_at: item.published_at || null,
    sector: item.sector || item.category || "General",
  }));
}

// =============================================================
// PAGE COMPONENT
// =============================================================

export default function HomePage() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  const load = async () => {
    try {
      const data = await fetchArticles();
      setArticles(data);
    } catch (err) {
      console.error("[HomePage] Failed to load articles:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 1. Initial Data Fetch
    load();

    // 2. Setup Supabase Realtime Listener for 'articles' table
    const articlesChannel = supabase
      .channel("realtime-articles-feed")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "articles" },
        (payload) => {
          console.log("⚡ [Realtime] New Article Received:", payload.new);

          const newItem = payload.new;
          const formatted: Article = {
            id: newItem.id || newItem.slug || String(Math.random()),
            slug: newItem.slug || newItem.id,
            headline: newItem.headline || newItem.title || "Untitled Article",
            excerpt: newItem.excerpt || newItem.abstract || null,
            featured_image_url:
              newItem.featured_image_url || newItem.cover_image_url || newItem.image_url || null,
            video_url: newItem.video_url || null,
            audio_url: newItem.audio_url || null,
            gif_url: newItem.gif_url || null,
            published_at: newItem.published_at || new Date().toISOString(),
            sector: newItem.sector || newItem.category || "General",
          };

          setArticles((prev) => {
            // Prevent duplication
            if (prev.some((item) => item.slug === formatted.slug || item.id === formatted.id)) {
              return prev;
            }
            return [formatted, ...prev];
          });

          // Reset user view to Page 1 so newly inserted article is instantly visible
          setPage(1);
        }
      )
      .subscribe((status) => {
        console.log("⚡ [Realtime] Subscription status:", status);
      });

    return () => {
      supabase.removeChannel(articlesChannel);
    };
  }, []);

  const totalPages = Math.max(1, Math.ceil(articles.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageItems = articles.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
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
          description="Run your ingestion worker to import the latest news."
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {pageItems.map((item) => (
            <NewsCard key={item.id} article={item} />
          ))}
        </div>
      )}

      <Pagination currentPage={safePage} totalPages={totalPages} onPageChange={setPage} />
    </div>
  );
}
