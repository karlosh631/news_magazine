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
 * Shape returned by /api/ai-search.
 *
 * The header used to call /api/search, which is the database query on its
 * own — so the endpoint that can actually recover a story (re-scrape the
 * outlets, then Google-search grounding) was never reached, and a
 * query with no match simply said "nothing published".
 *
 * /api/ai-search runs the same real search first and only falls through
 * when it finds nothing, so results are still grounded in real rows.
 */
type SearchSource = {
  headline: string;
  url: string | null;
  source: string | null;
  published_at: string | null;
};

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
  /** Present when the database had nothing and a fallback supplied links. */
  sources?: SearchSource[];
  /** Results came from a live web search, not from our ingested outlets. */
  grounded?: boolean;
  /** Results were re-scraped from the configured outlets. */
  resurrected?: boolean;
  summary?: string;
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
   *
   * This is also the rotate control: the feed re-composes the grid on
   * every re-poll, so a re-read is a rotation. A separate second button
   * was added and removed — two controls that both "make the news move"
   * is one too many.
   */
  const doRefresh = () => {
    setReloading(true);
    router.refresh();
    window.dispatchEvent(new CustomEvent("news-refresh"));
    window.setTimeout(() => setReloading(false), 700);
  };

  const closeSearch = () => {
    setSearchOpen(false);
    // Deliberately no blur(). Blurring the field while the overlay was
    // open is what made typing stop working — the reader's caret and
    // focus were both dropped the moment they dismissed or re-clicked.
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
    const tick = () => {
      // Never auto-refresh while the reader has something open or is
      // typing. router.refresh() re-renders the server tree underneath
      // the input, which is how a half-typed query used to vanish or
      // lose focus mid-word.
      if (searchOpen) return;
      if (menuOpen) return;
      const active = document.activeElement;
      if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return;
      if (document.hidden) return;
      if (window.getSelection()?.toString()) return;

      setReloading(true);
      router.refresh();
      // Same signal the refresh button sends, so the client-side feed
      // re-polls too. Without it the interval only re-rendered the
      // server components and the visible list never changed.
      window.dispatchEvent(new CustomEvent("news-refresh"));
      window.setTimeout(() => setReloading(false), 700);
    };

    intervalRef.current = setInterval(tick, AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [router, searchOpen, menuOpen]);

  useEffect(() => {
    const query = searchQuery.trim();

    // Empty query: clear results AND close the overlay. It used to stay
    // open showing the previous query's hits after the reader deleted
    // everything, which looked like search ignoring the edit.
    if (!query) {
      setAiSearch(null);
      setAiSearchError("");
      setSearchOpen(false);
      return;
    }

    // Only search while the overlay is open. Typing into a closed box
    // (then pressing Escape before results arrived) used to fire a
    // request for a query nobody ever sees, and the response landed
    // after the modal was gone.
    if (!searchOpen) return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setAiSearchLoading(true);
      setAiSearchError("");
      try {
        const response = await fetch("/api/ai-search", {
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
    }, 400);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [searchQuery, searchOpen]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;

    // Navigate only. The debounced effect above already issued the
    // search for this query, and /search runs its own server-side query,
    // so fetching again here meant three database round trips per Enter.
    setSearchOpen(false);
    setMenuOpen(false);
    router.push(`/search?q=${encodeURIComponent(query)}`);
  };

  /*
    z-[62] always — above the search overlay's backdrop (z-60) and panel
    (z-61).

    The reader types into this input, so it must never sit underneath
    the overlay. That was previously conditional on the overlay being
    open, which left the first click racing the z-index change; making
    it unconditional means the input is never covered at any point.

    The overlay still dims the whole viewport, so the modal reads as a
    layer over the page. The search bar staying bright above the dim is
    the standard command-palette look and shows the reader that focus
    is still where they left it.
  */
  return (
    <header className="relative sticky top-0 z-[62] border-b border-gray-200 bg-white md:bg-white/90 md:backdrop-blur">
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
            aria-label="Fetch the latest news and refresh the feed"
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
        count={aiSearch?.count ?? aiSearch?.articles?.length ?? 0}
        sources={aiSearch?.sources}
        grounded={aiSearch?.grounded}
        resurrected={aiSearch?.resurrected}
        onClose={closeSearch}
      />
    </header>
  );
}
