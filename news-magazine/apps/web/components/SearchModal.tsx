"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

import { formatNewsDate } from "@/lib/date";
import { cleanHeadline } from "@/lib/headline";

/**
 * Search results dropdown.
 *
 * This was an overlay: a fixed backdrop plus a centred dialog, with a
 * focus trap, a scroll lock and a focus guard that pulled focus back to
 * the input whenever it escaped. Every one of those is a way to stop a
 * reader typing — the reader types into the header's <input>, which
 * lives outside the dialog, and the dialog kept fighting for focus. The
 * symptom was always the same: type one word, the popup appears, and
 * nothing more can be entered.
 *
 * A dropdown anchored to the input removes the entire failure mode.
 * There is no backdrop to cover anything, nothing to trap, no scroll
 * lock, and focus is simply never moved — the reader keeps typing in the
 * same field the whole time.
 *
 * It is still a popup, still appears as you type, and still searches on
 * the same debounce. Only the containment changed.
 */

export type SearchHit = {
  id: string | null;
  /**
   * The story's own page on this site.
   *
   * Present for rows from our own articles table. Absent for rows the
   * ai-search endpoint recovered by re-scraping, which carry the
   * outlet's own URL instead — those have to render as an outbound link,
   * or the card would point at /article/undefined.
   */
  slug?: string | null;
  source_article_url?: string | null;
  headline: string;
  source_name_snapshot: string | null;
  published_at: string | null;
};

/**
 * A link supplied by a fallback rather than by our own articles table.
 *
 * Rendered visibly differently and never mixed in with ingested stories.
 * Presenting a live web result as if it came from the outlets we crawl
 * is the exact Stage 1 failure this pipeline was rewritten to end.
 */
export type SearchSource = {
  headline: string;
  url: string | null;
  source: string | null;
  published_at: string | null;
};

/**
 * Where a search hit goes.
 *
 * A hit that came from our own articles table has a slug, and it opens
 * HERE, as a regular story page, exactly like the same story in the
 * feed. That is the whole point of the site: the reader searched for a
 * story this site is running, and the result is that story.
 *
 * A hit that was recovered by re-scraping an outlet has no row here
 * yet, so there is no page of ours to open, and it opens at the outlet
 * that published it - in a new tab, and saying so.
 *
 * The bug this replaces: the endpoint left the slug out of its answer,
 * so EVERY result was treated as the second kind. The reader searched
 * for "balen", found the story on this site, clicked it, and was sent
 * to the publisher in a new tab.
 */
export function searchHitHref(hit: SearchHit): {
  href: string;
  external: boolean;
} {
  if (hit.slug) {
    return { href: `/article/${hit.slug}`, external: false };
  }
  return { href: hit.source_article_url ?? "#", external: true };
}

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
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  /**
   * Results belong to the query that produced them.
   *
   * While a new query is debouncing, the previous query's hits are still
   * on screen. Rendering them under the new query made the panel claim
   * "12 results" for something it had not searched yet.
   */
  const settled = query.trim().length > 0;
  const fallbackSources = (sources ?? []).filter((s) => Boolean(s.url));
  const showSources =
    settled && !loading && !error && hits.length === 0 && fallbackSources.length > 0;
  const showHits = settled && !loading && !error && hits.length > 0;

  // Escape closes. Nothing else touches focus.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Click away closes. A transparent full-screen listener rather than a
  // visible backdrop: it cannot be mistaken for a layer, and it cannot
  // intercept a click on the input, because the listener only acts on
  // clicks that land OUTSIDE the dropdown and outside the header.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (rootRef.current?.contains(target)) return;
      onClose();
    };
    // Capture, so it runs before a click on a result can navigate.
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      ref={rootRef}
      /*
        A SEARCH DROPDOWN, NOT A BANNER.

        It used to be `left-0 right-0`, so it stretched the whole width
        of the header: a 1,900-pixel-wide panel holding one result, with
        a vast empty space under it. It is now a panel the width of the
        thing it belongs to - the input - and no wider than 34rem, so it
        reads as a menu hanging off the search box rather than as a
        section of the page that happens to be open.
      */
      className="absolute right-0 top-full z-[63] mt-2 max-h-[min(26rem,70vh)] w-[min(34rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-gray-200 bg-white text-left shadow-xl shadow-black/5 dark:border-gray-700 dark:bg-gray-900"
    >
      <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-2 dark:border-gray-800">
        <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          {loading
            ? "Searching…"
            : error
              ? "Search failed"
              : !settled
                ? "Type to search"
                : hits.length === 0 && !showSources
                  ? `No matches for “${query.trim()}”`
                  : `${count} ${count === 1 ? "result" : "results"}`}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close search"
          /*
            48 pixels tall, like the rest of the site's controls: a
            finger dismissing the overlay should not have to find the
            word "Cancel" to land on it.
          */
          className="flex min-h-[48px] shrink-0 items-center rounded px-3 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-gray-100"
        >
          Cancel
        </button>
      </div>

      <div className="max-h-[min(21rem,60vh)] overflow-y-auto" aria-live="polite">
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
            <p>Nothing published in the last 48 hours matches “{query.trim()}”.</p>
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
                    onClick={onClose}
                    className="block px-4 py-2.5 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    <p className="line-clamp-2 text-sm font-medium leading-snug text-gray-900 dark:text-gray-100">
                      {cleanHeadline(source.headline)}
                    </p>
                    <p className="mt-1 text-[11px] text-gray-500">
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
              const { href, external } = searchHitHref(hit);
              const internal = !external;

              const body = (
                <>
                  <p className="line-clamp-2 text-sm font-medium leading-snug text-gray-900 dark:text-gray-100">
                    {cleanHeadline(hit.headline)}
                  </p>
                  <p className="mt-1 text-[11px] text-gray-500">
                    {hit.source_name_snapshot ?? ""}
                    {hit.published_at
                      ? ` · ${formatNewsDate(hit.published_at)}`
                      : ""}
                    {!internal && " · opens the source"}
                  </p>
                </>
              );

              return (
                <li key={hit.id ?? hit.slug ?? hit.headline}>
                  {internal ? (
                    <Link
                      href={href}
                      onClick={onClose}
                      className="block px-4 py-2.5 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                    >
                      {body}
                    </Link>
                  ) : (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={onClose}
                      className="block px-4 py-2.5 transition hover:bg-gray-50 dark:hover:bg-gray-800"
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
        <div className="border-t border-gray-100 bg-gray-50/60 px-4 py-2.5 dark:border-gray-800 dark:bg-gray-800/40">
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
  );
}