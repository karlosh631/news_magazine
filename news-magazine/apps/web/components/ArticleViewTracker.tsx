"use client";

import { useEffect, useRef } from "react";

/**
 * The reader's session id.
 *
 * One per browser tab, kept in `sessionStorage` so it lasts as long as
 * the tab does and not a moment longer. It is what turns "215 page
 * views" into "18 readers": without it every view is anonymous and
 * the distinct-session count is always zero.
 *
 * `sessionStorage` rather than `localStorage` on purpose. A reader
 * who leaves the tab open overnight and comes back is the same person
 * in the same session; a reader who closes the tab and opens it again
 * tomorrow is a new session, and the daily counts should say so.
 */
export function readerSessionId(): string {
  if (typeof window === "undefined") return "";

  const KEY = "new24hr_session";
  try {
    const existing = window.sessionStorage.getItem(KEY);
    if (existing) return existing;

    const fresh =
      typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(KEY, fresh);
    return fresh;
  } catch {
    // Private browsing, or a storage quota. A view without a session
    // is still a view; it just cannot be counted as a returning reader.
    return "";
  }
}

/**
 * Counts one view per article view, client-side.
 *
 * Stage 1 called increment_article_view() from the article page's render
 * body. A React Server Component render runs on every request: the
 * initial load, every client-side navigation, and Next.js's Link
 * prefetch — which fires for every link in the viewport. That inflated
 * the counter by roughly 4,800x per day.
 *
 * A ref guards against React 18 StrictMode double-invoking effects in
 * development.
 */
export function ArticleViewTracker({ articleId }: { articleId: string }) {
  const counted = useRef<string | null>(null);

  useEffect(() => {
    if (counted.current === articleId) return;
    counted.current = articleId;

    // Fire-and-forget: a failed view count must never surface to the
    // reader or block the page.
    void fetch("/api/articles/view", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        articleId,
        sessionId: readerSessionId(),
        referrer: typeof document === "undefined" ? null : document.referrer || null,
      }),
      keepalive: true,
    }).catch(() => undefined);
  }, [articleId]);

  return null;
}