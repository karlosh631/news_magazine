"use client";

import { useEffect, useRef } from "react";

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
      body: JSON.stringify({ articleId }),
      keepalive: true,
    }).catch(() => undefined);
  }, [articleId]);

  return null;
}