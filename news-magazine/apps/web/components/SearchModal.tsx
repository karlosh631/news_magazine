"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

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
  slug: string;
  headline: string;
  source_name_snapshot: string | null;
  published_at: string | null;
};

export function SearchModal({
  open,
  query,
  loading,
  error,
  hits,
  count,
  onClose,
  inputRef,
}: {
  open: boolean;
  query: string;
  loading: boolean;
  error: string;
  hits: SearchHit[];
  count: number;
  onClose: () => void;
  /**
   * The header's search <input>.
   *
   * Focus is deliberately NOT moved into the panel. Doing so pulled
   * keystrokes out of the input the moment the overlay appeared, so a
   * reader could type one character and then nothing — the next keypress
   * went to the dialog and was swallowed. Keeping focus in the input is
   * what lets them keep typing a whole query.
   */
  inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        inputRef?.current?.focus();
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

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Search results"
        className="fixed left-1/2 top-[10vh] z-[61] flex max-h-[78vh] w-[min(94vw,42rem)] -translate-x-1/2 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl outline-none dark:border-gray-700 dark:bg-gray-900"
      >
        <div className="flex items-center justify-between gap-3 border-b border-gray-200 px-5 py-3 dark:border-gray-700">
          <span className="truncate text-xs font-semibold uppercase tracking-wide text-gray-500">
            {loading
              ? "Searching…"
              : error
                ? "Search failed"
                : hits.length === 0 && query
                  ? `No matches for “${query}”`
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

          {!error && !loading && hits.length === 0 && (
            <div className="px-3 py-6 text-sm leading-6 text-gray-700 dark:text-gray-300">
              <p>
                Nothing published in the last 48 hours matches “{query}”.
              </p>
              <p className="mt-2 text-xs text-gray-500">
                Stories older than 48 hours are pruned. Press Enter in the
                search box to re-scrape every source — anything still
                published will come back.
              </p>
            </div>
          )}

          {!error && !loading && hits.length > 0 && (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {hits.slice(0, 10).map((hit) => (
                <li key={hit.id}>
                  <Link
                    href={`/article/${hit.slug}`}
                    onClick={onClose}
                    className="block rounded-lg px-3 py-3 transition hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    <p className="font-medium leading-snug text-gray-900 dark:text-gray-100">
                      {hit.headline}
                    </p>
                    <p className="mt-0.5 text-xs text-gray-500">
                      {hit.source_name_snapshot ?? ""}
                      {hit.published_at ? ` · ${hit.published_at.slice(0, 10)}` : ""}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        {!error && !loading && hits.length > 0 && (
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