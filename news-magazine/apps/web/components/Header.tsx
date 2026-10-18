"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SearchModal } from "@/components/SearchModal";

/**
 * Navigation categories.
 *
 * Passed in from the server layout, which reads the `categories` table.
 * Stage 1 hardcoded six slugs here while the database held eleven, so
 * five categories were unreachable and any new category needed a code
 * change to appear in the nav.
 */
export type NavCategory = {
  slug: string;
  name_en: string;
  name_ne: string | null;
};

const AUTO_REFRESH_MS = 15_000;

/**
 * Shape returned by /api/search.
 *
 * Stage 1 typed this as an AI brief (`summary` + `key_points` +
 * `sources`) because the endpoint fabricated one. The endpoint now runs
 * a real database search and returns matching articles, so the dropdown
 * renders actual headlines with real links instead of model output.
 */
type SearchResult = {
  query: string;
  count: number;
  articles: Array<{
    id: string;
    slug: string;
    headline: string;
    excerpt: string | null;
    summary_points: string[] | null;
    source_name_snapshot: string | null;
    source_article_url: string | null;
    published_at: string | null;
  }>;
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

export function Header({ categories }: { categories: NavCategory[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [language, setLanguage] = useState<"en" | "ne">("en");
  const [aiSearch, setAiSearch] = useState<SearchResult | null>(null);
  const [aiSearchError, setAiSearchError] = useState("");
  const [aiSearchLoading, setAiSearchLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  /**
   * Force a fresh server render AND tell the live feed to re-poll.
   * router.refresh() alone re-renders the Server Components but the
   * client feed keeps its own state, so the list would look unchanged.
   */
  const doRefresh = () => {
    setReloading(true);
    router.refresh();
    window.dispatchEvent(new CustomEvent("news-refresh"));
    window.setTimeout(() => setReloading(false), 700);
  };

  const closeSearch = () => {
    setSearchOpen(false);
    searchInputRef.current?.blur();
    setSearchQuery("");
    setAiSearch(null);
    setAiSearchError("");
  };

  const toggleLanguage = () => {
    setLanguage((current) => {
      const next = current === "en" ? "ne" : "en";
      localStorage.setItem("site-language", next);
      document.documentElement.lang = next;
      window.dispatchEvent(new CustomEvent("site-language-change", { detail: next }));
      return next;
    });
  };

  useEffect(() => {
    const stored = localStorage.getItem("site-language");
    if (stored === "en" || stored === "ne") {
      setLanguage(stored);
      document.documentElement.lang = stored;
    }
  }, []);

  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setReloading(true);
      router.refresh();
      window.setTimeout(() => setReloading(false), 700);
    }, AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [router]);

  useEffect(() => {
    const query = searchQuery.trim();
    if (!query) {
      setAiSearch(null);
      setAiSearchError("");
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setAiSearchLoading(true);
      setAiSearchError("");
      try {
        const response = await fetch("/api/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
          signal: controller.signal,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Search failed.");
        setAiSearch(payload as SearchResult);
      } catch (error) {
        if (!controller.signal.aborted) {
          setAiSearch(null);
          setAiSearchError(error instanceof Error ? error.message : "Search failed.");
        }
      } finally {
        if (!controller.signal.aborted) setAiSearchLoading(false);
      }
    }, 450);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [searchQuery]);

  const handleSearchSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;

    setAiSearchLoading(true);
    setAiSearchError("");
    try {
      const response = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Search failed.");
      setAiSearch(payload as SearchResult);
    } catch (error) {
      setAiSearch(null);
      setAiSearchError(error instanceof Error ? error.message : "Search failed.");
    } finally {
      setAiSearchLoading(false);
    }

    router.push(`/search?q=${encodeURIComponent(query)}`);
    closeSearch();
    setMenuOpen(false);
  };

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white md:bg-white/90 md:backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 gap-4">
        {/* Clicking the logo returns home AND forces a fresh fetch, so
            it behaves as a "back to latest" control rather than a plain
            link that might serve a cached render. */}
        <Link
          href="/"
          onClick={(event) => {
            if (pathname === "/") {
              // Already home: a plain navigation would be a no-op, so
              // force the refresh the reader is asking for.
              event.preventDefault();
              doRefresh();
            }
          }}
          className="flex shrink-0 items-center gap-2 font-bold text-gray-900 transition hover:text-blue-600 sm:text-xl"
        >
          <LogoIcon />
          <span className="truncate">NEWS</span>
        </Link>

        <nav className="hidden min-w-0 items-center gap-1 overflow-x-auto md:flex horizontal-scroll">
          {categories.map((cat) => {
            const href = `/category/${cat.slug}`;
            const isActive = pathname === href;
            return (
              <Link
                key={cat.slug}
                href={href}
                className={`whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-gray-900 text-white"
                    : "text-gray-700 hover:bg-gray-100 hover:text-black"
                }`}
              >
                {language === "ne" && cat.name_ne ? cat.name_ne : cat.name_en}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2">
          <form onSubmit={handleSearchSubmit} className="hidden sm:block">
            <input
              ref={searchInputRef}
              type="search"
              placeholder="Search news..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                if (e.target.value.trim()) setSearchOpen(true);
              }}
              className="h-9 w-40 rounded-md border border-gray-300 px-3 text-xs outline-none transition focus:w-56 focus:border-black focus:ring-1 focus:ring-black"
            />
          </form>

          <button
            type="button"
            onClick={toggleLanguage}
            aria-label="Toggle Nepali and English labels"
            className="h-9 min-w-9 rounded-md border border-gray-300 px-2 text-xs font-semibold text-gray-700 hover:bg-gray-100"
          >
            {language === "en" ? "ने" : "EN"}
          </button>

          <ThemeToggle />

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
              onChange={(e) => {
                setSearchQuery(e.target.value);
                if (e.target.value.trim()) setSearchOpen(true);
              }}
              className="h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-black focus:ring-1 focus:ring-black"
            />
          </form>

          {categories.map((cat) => {
            const href = `/category/${cat.slug}`;
            const isActive = pathname === href;
            return (
              <Link
                key={cat.slug}
                href={href}
                onClick={() => setMenuOpen(false)}
                className={`whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-gray-900 text-white"
                    : "text-gray-700 hover:bg-gray-100"
                }`}
              >
                {language === "ne" && cat.name_ne ? cat.name_ne : cat.name_en}
              </Link>
            );
          })}
        </nav>
      )}


      <SearchModal
        open={searchOpen}
        query={aiSearch?.query ?? searchQuery.trim()}
        loading={aiSearchLoading}
        error={aiSearchError}
        hits={aiSearch?.articles ?? []}
        count={aiSearch?.count ?? 0}
        onClose={closeSearch}
        inputRef={searchInputRef}
      />
    </header>
  );
}
