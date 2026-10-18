"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { NewsCard } from "@/components/NewsCard";
import type { LatestArticle } from "@/types/database";

/**
 * Live-updating article feed.
 *
 * Renders the server-rendered articles immediately (so the page is
 * never blank), then polls /api/live-news and merges in newer stories as
 * the ingestion worker publishes them.
 *
 * Why not the 1 second you asked for: the worker polls each outlet on
 * its own interval — 180s to 1800s — so at most a handful of outlets
 * publish a given minute. Polling our own database every second would be
 * ~3,600 requests an hour per open tab to surface the same handful of
 * stories a few seconds sooner. It costs Supabase quota and shows
 * nothing. 15s is comfortably inside the window where a new story
 * actually exists.
 *
 * Two things keep the page feeling alive between polls:
 *
 *   1. New arrivals are merged straight into the visible list. They used
 *      to be queued in `pending`, which was never rendered — so a story
 *      published while the tab was open was collected, counted, and then
 *      silently dropped. The "N new stories" pill that used to surface
 *      them was removed on request, so the merge is the only path.
 *
 *   2. When a poll returns nothing new, the list rotates. Ingestion runs
 *      on a 3–30 minute cycle per outlet and Render/GitHub add their own
 *      lag on top, so for minutes at a time the top of the page is
 *      genuinely static. Rotation changes the composition without
 *      inventing anything: the same real articles, reordered.
 */

/**
 * How often to re-poll our own database.
 *
 * This is a read against Supabase, not against the news publishers —
 * the worker is what talks to those, on its own 3-30 minute schedule.
 * Polling faster than the worker publishes re-fetches the same rows and
 * shows nothing; at one-second intervals it is ~3,600 requests an hour
 * per open tab, which is a good way to get the project throttled or
 * suspended by Supabase for everyone else's sake.
 *
 * 15s sits comfortably inside the worker's own cadence and costs four
 * cheap reads a minute per tab. Polling pauses while the tab is
 * hidden, and the newest stories appear the instant they are detected.
 */
const POLL_MS = 15_000;

/** How long a poll cycle can return nothing before we rotate. */
const ROTATE_AFTER_MS = 90_000;

/** How often the list re-composes while nothing new has arrived. */
const ROTATE_MS = 75_000;

/**
 * How many of the newest articles rotation must never touch.
 *
 * Nine is three rows of the 3-column grid. Everything above the fold and
 * one full row below it stays strictly newest-first; the tail reshuffles
 * so the page does not sit frozen while the worker catches up.
 *
 * Rotation used to promote the oldest quarter all the way to position 1,
 * which put a two-hour-old story above one published a minute ago. That
 * is the opposite of what "latest" means to a reader.
 */
const PINNED = 9;

type Rotate = "off" | "on";

export function LiveArticleFeed({
  initialArticles,
  emptyState,
  rotation = "on",
}: {
  initialArticles: LatestArticle[];
  emptyState?: { title: string; description: string };
  rotation?: Rotate;
}) {
  const [articles, setArticles] = useState(initialArticles);
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState<number | null>(null);
  // null until the reader picks a language, so the server-rendered
  // feed (which is unfiltered) is not yanked away on hydration.
  const [language, setLanguage] = useState<"en" | "ne" | null>(null);

  // Rotation is suppressed while the reader is actually reading: hovering
  // a card, holding a selection, or having the tab in the background.
  const [paused, setPaused] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);

  const ids = useRef(new Set(initialArticles.map((a) => a.id)));
  const lastArrivalAt = useRef<number | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setArticles(initialArticles);
    ids.current = new Set(initialArticles.map((a) => a.id));
    lastArrivalAt.current = null;
  }, [initialArticles]);

  // Respect the OS "reduce motion" setting. Reordering the grid under a
  // reader who asked for less movement is exactly the kind of thing that
  // setting exists to prevent.
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduceMotion(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

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
          lastArrivalAt.current = null;
          return;
        }

        // Only genuinely new ids, and only ones we do not already show.
        setArticles((current) => {
          const unseen = fresh.filter((a) => !ids.current.has(a.id));
          if (unseen.length === 0) return current;

          for (const article of unseen) ids.current.add(article.id);
          lastArrivalAt.current = Date.now();
          return [...unseen, ...current];
        });
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

  // ---- Rotation ----
  //
  // Only ever reorders. The pool of articles is fixed until a poll finds
  // something new.
  //
  // Declared above the event listeners below because the rotate button
  // calls this directly, and a const is in its temporal dead zone until
  // its declaration is evaluated.
  const rotate = useCallback((force = false) => {
    setArticles((current) => {
      if (current.length < PINNED + 3) return current;

      // Automatic rotation waits until the page has genuinely gone
      // static — ingestion runs on a 3-30 minute cycle per outlet, so
      // reshuffling every 75 seconds regardless would just be noise.
      // A manual click is an explicit request and is never second-guessed.
      if (!force) {
        const stale =
          lastArrivalAt.current === null ||
          Date.now() - lastArrivalAt.current > ROTATE_AFTER_MS;
        if (!stale) return current;
      }

      // The newest few are pinned. Reordering below them keeps the
      // "newest first" promise intact — a story published a minute ago
      // must not be pushed below a rotation just to make the page look
      // busy, which is the opposite of what a reader is checking for.
      const head = current.slice(0, PINNED);
      const rest = current.slice(PINNED);

      // Promote the oldest quarter of the remainder, preserving relative
      // order inside both halves. A full shuffle is unreadable; a
      // one-item nudge is invisible.
      const cut = Math.max(1, Math.round(rest.length / 4));
      return [...head, ...rest.slice(rest.length - cut), ...rest.slice(0, rest.length - cut)];
    });
  }, []);

  // The header refresh button asks the feed to re-poll immediately.
  useEffect(() => {
    const onManualRefresh = () => void check(language, true);
    window.addEventListener("news-refresh", onManualRefresh);
    return () => window.removeEventListener("news-refresh", onManualRefresh);
  }, [check, language]);

  // The header rotate button does both jobs in one click: fetch whatever
  // the worker has published since the last poll, then re-compose the
  // page. Sending only the refresh signal left the grid in the same
  // order even though it had just re-read the database, which is what
  // made the button look broken.
  useEffect(() => {
    const onRotate = async () => {
      await check(language, true);
      lastArrivalAt.current = Date.now() - ROTATE_AFTER_MS;
      rotate(true);
    };
    window.addEventListener("news-rotate", onRotate);
    return () => window.removeEventListener("news-rotate", onRotate);
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


  useEffect(() => {
    if (rotation === "off" || reduceMotion) return;

    const timer = window.setInterval(() => {
      if (paused) return;
      if (typeof document !== "undefined" && document.hidden) return;
      if (window.getSelection()?.toString()) return; // reader is selecting text
      rotate();
    }, ROTATE_MS);

    return () => window.clearInterval(timer);
  }, [rotate, rotation, reduceMotion, paused]);

  // Hovering the grid pauses rotation. Grabbing a card is not the moment
  // to have the floor move under it.
  const pause = useCallback((on: boolean) => {
    if (typeof document !== "undefined" && document.hidden) return;
    setPaused(on);
  }, []);

  const visible = useMemo(() => articles, [articles]);

  if (visible.length === 0) {
    return (
      <EmptyState
        title={emptyState?.title ?? "No stories yet"}
        description={
          emptyState?.description ??
          "The ingestion worker has not published anything yet."
        }
      />
    );
  }

  return (
    <div
      ref={gridRef}
      onMouseEnter={() => pause(true)}
      onMouseLeave={() => pause(false)}
    >
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((article) => (
          <NewsCard
            key={article.id}
            article={article}
            language={language ?? "en"}
          />
        ))}
      </div>

      {/* Screen readers get the polling state without it costing a
          re-render every second. */}
      <p className="sr-only" role="status" aria-live="polite">
        {checking ? "Checking for new stories." : "Feed up to date."}
      </p>
    </div>
  );
}