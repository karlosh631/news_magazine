"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SearchModal } from "@/components/SearchModal";
import type { DailyInfo } from "@/lib/daily-info";

/**
 * Navigation categories.
 *
 * Passed in from the server layout, which reads the `categories` table.
 * Stage 1 hardcoded six slugs here while the database held eleven, so
 * five categories were unreachable and any new category needed a code
 * change to appear in the nav.
 */
/**
 * A glyph per kind of sky.
 *
 * Characters rather than images on purpose: an icon font would be
 * another request, another thing to load, and another way for the
 * header to look broken while the page is still loading. The glyphs
 * are in the reader's own font and they are already there.
 */
const WEATHER_ICON: Record<string, string> = {
  clear: "☀",
  cloud: "☁",
  rain: "☂",
  snow: "❄",
  storm: "⚡",
  fog: "🌫",
};

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

export function Header({
  categories,
  dailyInfo,
}: {
  categories: NavCategory[];
  /**
   * The day's date, almanac and weather.
   *
   * Rendered IN the header, in the space between the navigation and
   * the search box, rather than as a row of its own: a row under the
   * header is a row of its own, and it costs 30px of every screen and
   * pushes the news down.
   */
  dailyInfo?: DailyInfo | null;
}) {
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
    /*
      A real reload, not router.refresh().

      router.refresh() re-renders the server tree in place, which is
      nicer when it works — no white flash, scroll position kept. But
      the reader pressing this is asking a specific question: "is the
      news newer than what I'm looking at?" An in-place re-render can
      reuse client state that was already hydrated, and the answer is
      then ambiguous. A navigation reload rebuilds every component from
      the database, so the button is never arguable.

      The Supabase reads are cache: "no-store" now, so this genuinely
      costs one fresh render rather than re-reading a cached body.
    */
    setReloading(true);
    window.location.reload();
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
    // Side effects belong OUTSIDE the state updater.
    //
    // These used to run inside the function passed to setLanguage, which
    // React does not promise to call exactly once — it re-invokes
    // updaters under StrictMode and may call them again on an interrupted
    // render. Writing localStorage, mutating <html lang> and dispatching
    // an event from there means those can happen twice, and dispatching
    // synchronously during the render phase is what produced
    // "Cannot update a component (ArticleTranslation) while rendering a
    // different component (Header)". That error tears down the subtree,
    // which is why the theme and refresh buttons beside it stopped
    // responding.
    const next = language === "en" ? "ne" : "en";
    setLanguage(next);
    localStorage.setItem("site-language", next);
    document.documentElement.lang = next;
    window.dispatchEvent(new CustomEvent("site-language-change", { detail: next }));
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

      // The admin panel is NOT auto-refreshed.
      //
      // It is a page of numbers that change as the worker runs, so
      // refreshing it every fifteen seconds moved the whole page under
      // the operator's hands: the counts changed, the charts redrew to
      // a different shape, and the rows they were reading shifted
      // down. A dashboard that moves while it is being read cannot be
      // read.
      //
      // The panel carries its own "generated at" timestamp, and the
      // refresh button in the header still works, so the operator
      // decides when it updates.
      if (pathname === "/admin" || pathname === "/login") return;

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
  }, [router, searchOpen, menuOpen, pathname]);

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

        {/*
          The day's information, in the space the header had.

          It sits between the navigation and the search box, so it
          costs the page no height and no row. The tithi and the
          weather are the two things a Nepali reader checks before
          they read the news, and they were nowhere on the site.

          Hidden on a phone: there is no space for it between the logo
          and the menu button, and a header that has to scroll
          horizontally to show the date is worse than no date.
        */}
        {dailyInfo ? (
          <div className="hidden shrink-0 items-center gap-3 text-[11px] text-gray-500 xl:flex dark:text-gray-400">
            {dailyInfo.panchang?.bsLabel ? (
              <span className="whitespace-nowrap">
                {dailyInfo.panchang.bsLabel}
                {dailyInfo.panchang.tithi
                  ? ` · ${dailyInfo.panchang.tithiFull ?? dailyInfo.panchang.tithi}`
                  : ""}
              </span>
            ) : (
              <span className="whitespace-nowrap">
                {new Date(`${dailyInfo.today}T00:00:00Z`).toLocaleDateString(
                  "en-GB",
                  { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }
                )}
              </span>
            )}
            {dailyInfo.weather ? (
              <span className="flex items-center gap-1 whitespace-nowrap">
                <span aria-hidden="true">
                  {WEATHER_ICON[dailyInfo.weather.icon ?? "clear"]}
                </span>
                {dailyInfo.weather.temperature !== null ? (
                  <span className="tabular-nums">
                    {dailyInfo.weather.temperature}°C
                  </span>
                ) : null}
                {dailyInfo.weather.description ? (
                  <span>{dailyInfo.weather.description}</span>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="flex items-center gap-2">
          <form onSubmit={handleSearchSubmit} className="hidden sm:block">
            <input
              ref={searchInputRef}
              type="search"
              /*
                id and name, so the field is a form control the browser
                can label, autofill and restore rather than an
                unlabelled box. Lighthouse flags a search input with
                neither, and it is right to: a reader who has typed a
                search once should not have to type it again after a
                reload, and a password manager cannot offer to fill a
                field it cannot name.

                The two inputs are the same control in two layouts, so
                they get the same id — only one is ever in the DOM's
                accessibility tree at a time (the other is behind
                `md:hidden` or `hidden md:block`), and the mobile one
                is only mounted when the menu is open.
              */
              id="header-search"
              name="q"
              autoComplete="off"
              aria-label="Search news"
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
              /* The same control as the desktop one, in the mobile
               * menu. A DIFFERENT id, deliberately: on a phone the
               * desktop form is still in the DOM (hidden by CSS, not
               * unmounted) and this one is mounted when the menu
               * opens, so two elements sharing an id would both be in
               * the accessibility tree at once. Same `name` is fine —
               * that is a form-field name, and only one of the two is
               * ever submitted. */
              id="header-search-mobile"
              name="q"
              autoComplete="off"
              aria-label="Search news"
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
