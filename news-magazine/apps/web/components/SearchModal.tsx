"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

import { formatNewsDate } from "@/lib/date";

/**
 * Search results overlay.
 *
 * Results appear in a centred modal over a dimmed backdrop rather than
 * inline under the header, so they do not shove the page down while the
 * reader is still typing. Click a result to open it, "Cancel", the
 * backdrop, or Escape to dismiss.
 */

export type SearchHit = {
  id: string;
  /**
   * Present for rows from our own articles table. Absent for rows the
   * ai-search endpoint recovered by re-scraping, which carry the
   * outlet's own URL instead — those have to render as an outbound link,
   * or the card would point at /article/undefined.
   */
  slug?: string;
  source_article_url?: string | null;
  headline: string;
  source_name_snapshot: string | null;
  published_at: string | null;
};

/**
 * A link supplied by a fallback rather than by our own articles table.
 *
 * These are rendered visibly differently and never mixed in with ingested
 * stories. Presenting a live web result as if it came from the 20+
 * outlets we crawl is the exact Stage 1 failure this pipeline was
 * rewritten to end.
 */
export type SearchSource = {
  headline: string;
  url: string | null;
  source: string | null;
  published_at: string | null;
};

export function SearchModal({
  open,
  query,
  loading,
  error,
  hits,
  count,
  sources,
  grounded,
  resurrected,
  onClose,
  inputRef,
}: {
  open: boolean;
  query: string;
  loading: boolean;
  error: string;
  hits: SearchHit[];
  count: number;
  sources?: SearchSource[];
  /** The results came from a live web search, not from our archive. */
  grounded?: boolean;
  /** The results were re-scraped from the configured outlets. */
  resurrected?: boolean;
  onClose: () => void;
  /**
   * The header's search <input>.
   *
   * Focus is deliberately NOT moved into the panel, and a guard pulls it
   * back if anything steals it — the reader types into the input, not
   * into the overlay.
   */
  inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  /**
   * Results belong to the query that produced them.
   *
   * While a new query is debouncing, the previous query's hits are still
   * on screen. Rendering them under the new query made the panel claim
   * "12 results" for something it had not searched yet, so a stale list
   * is treated as no list at all.
   */
  const settled = query.trim().length > 0;
  const fallbackSources = (sources ?? []).filter((s) => Boolean(s.url));
  const showSources =
    settled && !loading && !error && hits.length === 0 && fallbackSources.length > 0;
  const showHits = settled && !loading && !error && hits.length > 0;

  // Escape closes, Tab stays inside the dialog. The panel declares
  // aria-modal, which promises the rest of the page is unreachable — it
  // was not: Tab walked straight off into the page behind the backdrop.
  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        inputRef?.current?.focus();
        return;
      }

      if (event.key !== "Tab") return;

      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable || focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, inputRef]);

  // Lock background scroll while open.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  /**
   * Focus guard.
   *
   * The reader types into the header's <input>, not into this panel, so
   * focus has to STAY there. The panel is declared `aria-modal`, which
   * in principle means focus cannot leave it — in practice the browser
   * does not enforce that, and anything that steals focus (a click
   * landing on the backdrop, a re-render, the browser's own focus
   * restoration) left the reader typing into nothing.
   *
   * This is the command-palette pattern: while the overlay is open, any
   * focus that lands outside both the input and the panel is pulled
   * straight back to the input.
   */
  useEffect(() => {
    if (!open) return;
    const input = inputRef?.current;
    if (!input) return;

    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (target === input) return;
      if (panelRef.current?.contains(target)) return;

      input.focus();
    };

    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, [open, inputRef]);

  if (!open) return null;

  return (
    <>
      {/*
        Starts below the header (h-16), not at inset-0.

        This used to cover the whole viewport at z-60 while the header
        sits at z-50, so the very input the reader was typing into ended
        up underneath the overlay. The next click hit the backdrop, which
        closed the modal and blurred the field — "the popup opens but I
        can't type in it".
      */}
      <div
        className="fixed inset-x-0 bottom-0 top-16 z-[60] bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Search results"
        className="fixed left-1/2 top-20 z-[61] flex max-h-[72vh] w-[min(94vw,42rem)] -translate-x-1/2 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl outline-none dark:border-gray-700 dark:bg-gray-900"
      >
        <div className="flex items-center justify-between gap-3 border-b border-gray-200 px-5 py-3 dark:border-gray-700">
          <span className="truncate text-xs font-semibold uppercase tracking-wide text-gray-500">
            {loading
              ? "Searching…"
              : error
                ? "Search failed"
                : !settled
                  ? "Type to search"
                  : hits.length === 0
                    ? `No matches for “${query.trim()}”`
                    : `${count} ${count === 1 ? "result" : "results"}`}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close search"
            className="shrink-0 rounded px-2 py-1 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-gray-100"
          >
            Cancel
          </button>
        </div>

        <div className="min-h-[8rem] flex-1 overflow-y-auto px-2 py-2" aria-live="polite">
          {error && (
            <p className="px-3 py-4 text-sm text-red-700 dark:text-red-400">{error}</p>
          )}

          {!error && loading && (
            <div className="space-y-3 px-3 py-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-gray-100 dark:bg-gray-800" />
              ))}
            </div>
          )}

          {!error && !loading && settled && hits.length === 0 && !showSources && (
            <div className="px-3 py-6 text-sm leading-6 text-gray-700 dark:text-gray-300">
              <p>
                Nothing published in the last 48 hours matches “{query.trim()}”.
              </p>
              <p className="mt-2 text-xs text-gray-500">
                Stories older than 48 hours are pruned. Press Enter in the
                search box to open the full results page, which re-scrapes
                every configured outlet for this query.
              </p>
            </div>
          )}

          {showSources && (
            <div>
              <p className="px-3 pb-2 pt-1 text-xs leading-5 text-gray-600 dark:text-gray-400">
                {grounded
                  ? "Nothing in our archive matches. These are live web results — they are NOT from the outlets this site crawls."
                  : resurrected
                    ? "Not in the 48-hour window, but re-fetched from the outlets this site crawls."
                    : "Found outside the 48-hour window."}
              </p>
              <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                {fallbackSources.slice(0, 8).map((source, index) => (
                  <li key={`${source.url}-${index}`}>
                    <a
                      href={source.url ?? "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block rounded-lg px-3 py-3 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                    >
                      <p className="font-medium leading-snug text-gray-900 dark:text-gray-100">
                        {source.headline}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-500">
                        {source.source ?? "External source"}
                        {source.published_at
                          ? ` · ${formatNewsDate(source.published_at)}`
                          : ""}
                      </p>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {showHits && (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {hits.slice(0, 10).map((hit) => {
                // Our own rows link internally; anything the fallback
                // recovered links out to the outlet that published it.
                const internal = Boolean(hit.slug);
                const href = internal
                  ? `/article/${hit.slug}`
                  : (hit.source_article_url ?? "#");

                const body = (
                  <>
                    <p className="font-medium leading-snug text-gray-900 dark:text-gray-100">
                      {hit.headline}
                    </p>
                    <p className="mt-0.5 text-xs text-gray-500">
                      {hit.source_name_snapshot ?? ""}
                      {hit.published_at
                        ? ` · ${formatNewsDate(hit.published_at)}`
                        : ""}
                      {!internal && " · opens the source"}
                    </p>
                  </>
                );

                return (
                  <li key={hit.id}>
                    {internal ? (
                      <Link
                        href={href}
                        onClick={onClose}
                        className="block rounded-lg px-3 py-3 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                      >
                        {body}
                      </Link>
                    ) : (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={onClose}
                        className="block rounded-lg px-3 py-3 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                      >
                        {body}
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {showHits && (
          <div className="border-t border-gray-200 px-5 py-3 dark:border-gray-700">
            <Link
              href={`/search?q=${encodeURIComponent(query)}`}
              onClick={onClose}
              className="text-sm font-semibold text-blue-700 hover:underline dark:text-blue-400"
            >
              Open full results →
            </Link>
          </div>
        )}
      </div>
    </>
  );
}