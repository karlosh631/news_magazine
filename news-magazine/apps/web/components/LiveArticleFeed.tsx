"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { NewsCard } from "@/components/NewsCard";
import type { LatestArticle } from "@/types/database";

/**
 * Live-updating article feed.
 *
 * Renders the server-rendered articles immediately (so the page is
 * never blank), then polls /api/live-news and swaps in newer stories as
 * the ingestion worker publishes them.
 *
 * Why 30 seconds and not the 1 second you asked for: the worker polls
 * each outlet on its own interval — 180s to 1800s — so at most a handful
 * of outlets publish a given minute. Polling our own database every
 * second would be ~2,900 requests an hour per open tab to surface the
 * same handful of stories a few seconds sooner. It costs Supabase
 * quota and shows nothing. 30s is well inside the window where a new
 * story actually exists.
 *
 * New arrivals are queued behind a "N new stories" pill rather than
 * yanking the list out from under someone mid-read.
 */

const POLL_MS = 30_000;

export function LiveArticleFeed({
  initialArticles,
  emptyState,
}: {
  initialArticles: LatestArticle[];
  emptyState?: { title: string; description: string };
}) {
  const [articles, setArticles] = useState(initialArticles);
  const [pending, setPending] = useState<LatestArticle[]>([]);
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState<number | null>(null);
  // null until the reader picks a language, so the server-rendered
  // feed (which is unfiltered) is not yanked away on hydration.
  const [language, setLanguage] = useState<"en" | "ne" | null>(null);

  const ids = useRef(new Set(initialArticles.map((a) => a.id)));

  useEffect(() => {
    setArticles(initialArticles);
    ids.current = new Set(initialArticles.map((a) => a.id));
    setPending([]);
  }, [initialArticles]);

  // Pick up the language the reader chose in the header. Stage 1
  // dispatched this event but nothing listened to it, so the toggle
  // changed nothing beyond the nav labels.
  useEffect(() => {
    const stored = localStorage.getItem("site-language");
    if (stored === "en" || stored === "ne") setLanguage(stored);

    const onChange = (event: Event) => {
      const next = (event as CustomEvent<"en" | "ne">).detail;
      if (next === "en" || next === "ne") setLanguage(next);
    };
    window.addEventListener("site-language-change", onChange);
    return () => window.removeEventListener("site-language-change", onChange);
  }, []);

  const check = useCallback(
    async (lang: "en" | "ne" | null, reset = false) => {
      setChecking(true);
      try {
        const params = new URLSearchParams({ limit: "30" });
        if (lang) params.set("language", lang);

        const response = await fetch(`/api/live-news?${params}`, {
          cache: "no-store",
        });
        if (!response.ok) return;

        const payload = (await response.json()) as { articles?: LatestArticle[] };
        const fresh = payload.articles ?? [];
        setLastChecked(Date.now());

        if (reset) {
          setArticles(fresh);
          ids.current = new Set(fresh.map((a) => a.id));
          setPending([]);
          return;
        }

        // Only genuinely new ids, and only ones we do not already show.
        const unseen = fresh.filter((a) => !ids.current.has(a.id));
        if (unseen.length > 0) {
          setPending((current) => {
            const merged = [...unseen, ...current];
            const seen = new Set<string>();
            return merged.filter((a) => {
              if (seen.has(a.id)) return false;
              seen.add(a.id);
              return true;
            });
          });
        }
      } catch {
        // Offline or the API restarting: the list stays as-is.
      } finally {
        setChecking(false);
      }
    },
    []
  );

  // Reload immediately when the language changes so the swap is visible
  // straight away rather than waiting for the next poll tick.
  const firstLanguageRender = useRef(true);
  useEffect(() => {
    if (firstLanguageRender.current) {
      firstLanguageRender.current = false;
      return;
    }
    void check(language, true);
  }, [language, check]);

  // The header refresh button asks the feed to re-poll immediately.
  useEffect(() => {
    const onManualRefresh = () => void check(language, true);
    window.addEventListener("news-refresh", onManualRefresh);
    return () => window.removeEventListener("news-refresh", onManualRefresh);
  }, [check, language]);

  useEffect(() => {
    const timer = window.setInterval(() => void check(language), POLL_MS);
    // Pause polling when the tab is hidden; resume and catch up on return.
    const onVisible = () => {
      if (document.visibilityState === "visible") void check(language);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [check, language]);

  return (
    <div>
      {articles.length === 0 ? (
        <EmptyState
          title={emptyState?.title ?? "No stories yet"}
          description={emptyState?.description ?? "The ingestion worker has not published anything yet."}
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {articles.map((article) => (
            <NewsCard key={article.id} article={article} language={language ?? "en"} />
          ))}
        </div>
      )}
    </div>
  );
}