"use client";

import { useCallback, useMemo, useState } from "react";

import { NewsCard } from "@/components/NewsCard";
import type { LatestArticle } from "@/types/database";

/**
 * A news grid that swaps out any card whose artwork turns out to be dead.
 *
 * This exists because `onImageUnavailable` was only ever passed by
 * components/LiveArticleFeed.tsx. Every other surface that renders a
 * NewsCard — app/category/[slug]/page.tsx, app/search/page.tsx and the
 * side columns of components/ReelsFeed.tsx — passed no callback at all,
 * so the one mechanism the card had for asking to be replaced was
 * unreachable. The card fell back to its own "No image available" panel
 * and stayed there: on /category/reels, which repeats each column 2-10
 * times for the drift loop, that meant a single imageless story could be
 * painted up to twenty times and never went away.
 *
 * The server-side pass in lib/feed-images.ts (`dropImageless`) handles the
 * rows we can know about without making a request — a row with no media
 * URL in any column. This handles the rows only a browser can disprove:
 * an image URL that is present, looks right, and 403s or 404s when the
 * card actually asks for it. Measured against the live feed while this was
 * written, seven of the newest sixty articles carried no image at all —
 * and separately, a stored URL that has since gone 404 cannot be caught by
 * any amount of server-side reasoning about URL shape.
 *
 * Both halves are needed. Neither subsumes the other, and the order
 * matters: this runs after hydration, so without the server pass the
 * reader would still see the placeholder on the first paint.
 *
 * The floor is the same one LiveArticleFeed keeps — if every card in the
 * list is imageless, they all stay. A grid of honest panels on a feed
 * that has no pictures is a bad page; an empty page is a worse one, and
 * it is a lie about what was actually published.
 */
export function ImagelessGrid({
  articles,
  language = "en",
}: {
  articles: LatestArticle[];
  language?: "en" | "ne";
}) {
  const [imageless, setImageless] = useState<Set<string>>(() => new Set());

  const markImageless = useCallback((articleId: string) => {
    setImageless((prev) => {
      if (prev.has(articleId)) return prev;
      const next = new Set(prev);
      next.add(articleId);
      return next;
    });
  }, []);

  const visible = useMemo(
    () =>
      imageless.size === 0
        ? articles
        : articles.filter((article) => !imageless.has(article.id)),
    [articles, imageless]
  );

  // Nothing may be dropped if dropping would empty the page. Reached by
  // rendering the unfiltered list rather than by checking here, so the
  // card still reports its id and the set settles on what is actually
  // on screen.
  const shown = visible.length > 0 ? visible : articles;

  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
      {shown.map((article) => (
        <NewsCard
          key={article.id}
          article={article}
          language={language}
          onImageUnavailable={markImageless}
        />
      ))}
    </div>
  );
}

/**
 * The list half of `ImagelessGrid`, for surfaces that lay the cards out
 * themselves.
 *
 * components/ReelsFeed.tsx needs this: its columns are not a grid, and it
 * repeats each list several times over for the seamless drift. Filtering
 * has to happen once, on the source list, or every copy of a dropped card
 * would keep painting — and reporting the same id once per copy would make
 * the callback fire in a loop of its own making.
 */
export function useImagelessFilter(articles: LatestArticle[]) {
  const [imageless, setImageless] = useState<Set<string>>(() => new Set());

  const markImageless = useCallback((articleId: string) => {
    setImageless((prev) => {
      if (prev.has(articleId)) return prev;
      const next = new Set(prev);
      next.add(articleId);
      return next;
    });
  }, []);

  const visible = useMemo(() => {
    if (imageless.size === 0) return articles;
    const kept = articles.filter((article) => !imageless.has(article.id));
    return kept.length > 0 ? kept : articles;
  }, [articles, imageless]);

  return { visible, markImageless } as const;
}