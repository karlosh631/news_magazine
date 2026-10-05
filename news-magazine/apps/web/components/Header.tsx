"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

const CATEGORIES = [
  { label: "National", slug: "national" },
  { label: "Politics", slug: "politics" },
  { label: "Business", slug: "business" },
  { label: "Technology", slug: "technology" },
  { label: "Sports", slug: "sports" },
  { label: "Entertainment", slug: "entertainment" },
];

const AUTO_REFRESH_MS = 15_000;

type AiSearchResult = {
  query: string;
  summary: string;
  key_points: string[];
  confidence: "high" | "medium" | "low";
  sources: Array<{ headline: string; url: string | null; published_at: string | null }>;
};

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
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className={spinning ? "animate-spin" : ""} aria-hidden="true">
      <path d="M20 11a8 8 0 1 0-2.34 5.66" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M20 5v6h-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Header() {
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [aiSearch, setAiSearch] = useState<AiSearchResult | null>(null);
  const [aiSearchError, setAiSearchError] = useState("");
  const [aiSearchLoading, setAiSearchLoading] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const doRefresh = () => {
    setReloading(true);
    router.refresh();
    window.setTimeout(() => setReloading(false), 700);
  };

  useEffect(() => {
    intervalRef.current = setInterval(doRefresh, AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const handleSearchSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;

    setAiSearchLoading(true);
    setAiSearchError("");
    try {
      const response = await fetch("/api/ai-search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "AI search failed.");
      setAiSearch(payload as AiSearchResult);
    } catch (error) {
      setAiSearch(null);
      setAiSearchError(error instanceof Error ? error.message : "AI search failed.");
    } finally {
      setAiSearchLoading(false);
    }

    router.push(`/search?q=${encodeURIComponent(query)}`);
    setMenuOpen(false);
  };

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white md:bg-white/90 md:backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 gap-4">
        <Link
          href="/"
          className="flex items-center gap-2 font-bold text-gray-900 transition hover:text-blue-600 sm:text-xl shrink-0"
        >
          <LogoIcon />
          <span className="truncate">Nepal News &amp; Magazine</span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {CATEGORIES.map((cat) => {
            const href = `/category/${cat.slug}`;
            const isActive = pathname === href;
            return (
              <Link
                key={cat.slug}
                href={href}
                className={`rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-gray-900 text-white"
                    : "text-gray-700 hover:bg-gray-100 hover:text-black"
                }`}
              >
                {cat.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2">
          <form onSubmit={handleSearchSubmit} className="hidden sm:block">
            <input
              type="search"
              placeholder="Search news..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-9 w-40 rounded-md border border-gray-300 px-3 text-xs outline-none transition focus:w-56 focus:border-black focus:ring-1 focus:ring-black"
            />
          </form>

          <button
            type="button"
            onClick={doRefresh}
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
        <nav className="flex flex-col gap-2 border-t border-gray-200 bg-white px-4 py-3 md:hidden">
          <form onSubmit={handleSearchSubmit} className="mb-1">
            <input
              type="search"
              placeholder="Search news..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-black focus:ring-1 focus:ring-black"
            />
          </form>

          {CATEGORIES.map((cat) => {
            const href = `/category/${cat.slug}`;
            const isActive = pathname === href;
            return (
              <Link
                key={cat.slug}
                href={href}
                onClick={() => setMenuOpen(false)}
                className={`rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-gray-900 text-white"
                    : "text-gray-700 hover:bg-gray-100"
                }`}
              >
                {cat.label}
              </Link>
            );
          })}
        </nav>
      )}

      {(aiSearchLoading || aiSearch || aiSearchError) && (
        <section className="border-t border-gray-200 bg-gray-50 px-4 py-4" aria-live="polite">
          <div className="mx-auto max-w-7xl">
            {aiSearchLoading && <p className="text-sm text-gray-600">Preparing an AI news brief...</p>}
            {aiSearchError && <p className="text-sm text-red-700">{aiSearchError}</p>}
            {aiSearch && !aiSearchLoading && (
              <div className="grid gap-3 md:grid-cols-[1fr_auto]">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    AI brief - {aiSearch.confidence} confidence
                  </p>
                  <p className="mt-1 text-sm leading-6 text-gray-800">{aiSearch.summary}</p>
                  {aiSearch.key_points.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
                      {aiSearch.key_points.map((point) => <li key={point}>{point}</li>)}
                    </ul>
                  )}
                </div>
                <div className="text-xs text-gray-500">
                  <p className="font-semibold text-gray-700">Sources</p>
                  {aiSearch.sources.slice(0, 3).map((source) => (
                    <a
                      key={`${source.headline}-${source.url}`}
                      href={source.url || `/search?q=${encodeURIComponent(aiSearch.query)}`}
                      target={source.url ? "_blank" : undefined}
                      rel={source.url ? "noreferrer" : undefined}
                      className="mt-1 block max-w-xs truncate hover:text-blue-700"
                    >
                      {source.headline}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}
    </header>
  );
}
