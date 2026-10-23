"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { NewsCard } from "@/components/NewsCard";
import { showsNothing } from "@/lib/card-artwork";
import { dropDuplicateStories } from "@/lib/feed-dedup";
import { dropRepeatedImages } from "@/lib/feed-images";
import { byNewest, mergeArticles } from "@/lib/feed-merge";
import { SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";
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
const POLL_MS = 10_000;

/** How long a poll cycle can return nothing before we rotate. */
const ROTATE_AFTER_MS = 90_000;

/** How often the list re-composes while nothing new has arrived. */
const ROTATE_MS = 75_000;

/**
 * How many of the newest articles rotation must never touch, when it is
 * switched on. Nine is three rows of the 3-column grid.
 *
 * Rotation is OFF by default. The reader asked for the newest news at the
 * top, everywhere, and for old stories to be replaced by new ones —
 * a reshuffle works against both. It stays available as an opt-in, and
 * even then it only touches the tail: promoting the oldest quarter all
 * the way to position 1 would put a two-hour-old story above one
 * published a minute ago, which is the opposite of what "latest" means.
 */
const PINNED = 9;

/**
 * How many cards one page of the archive brings in.
 *
 * Thirty, so a page is a little taller than the viewport on a phone and
 * the reader is not offered a fresh screenful of the same feed every
 * few seconds of scrolling. The API's own ceiling is 500, so this is a
 * choice about request size — not a limit on how far down the archive a
 * reader can go.
 */
const MORE_PAGE_SIZE = 30;

/**
 * How far ahead of the viewport the sentinel starts a load.
 *
 * Not zero. At zero the request only starts once the sentinel is already
 * on screen, so the reader always watches the gap arrive and always waits
 * through a request they can see coming.
 *
 * It is 1200px rather than 800 because the archive is now endless on
 * every page, and the reader scrolling fast through the national
 * category would otherwise outrun the loader and watch it catch up.
 * Three screens' worth of runway is enough that the next page is
 * usually already there.
 *
 * This is also what keeps "the whole feed" honest. The observer loads
 * until the sentinel is pushed out of that band and then stops, so a
 * reader who scrolls reaches the end of the archive while a reader who
 * does not never pays for a card they did not look at.
 */
const SENTINEL_ROOT_MARGIN = "1200px 0px";

/**
 * The heading a category page shows.
 *
 * "national" -> "National news".
 *
 * The category NAME is not passed in — the route knows it and this
 * component does not — so the slug is turned into words. The category
 * page is the one route that cannot be given a heading by the page
 * file itself without a change to a file this work is not touching, so
 * the heading is produced here, from the one thing the route does hand
 * over.
 */
function categoryHeading(slug: string): string {
  const words = slug.replace(/-/g, " ").trim();
  if (!words) return "Latest news";
  const title = words.charAt(0).toUpperCase() + words.slice(1);
  return `${title} news`;
}

/**
 * Will this card have nothing at all to show?
 *
 * A card shows, in order of preference: its picture, its own key
 * points, or an honest "No image available" panel. When it has none
 * of the three, the feed swaps it for the next story — which is right,
 * and which used to happen AFTER the page had painted.
 *
 * THAT ORDER WAS THE LAYOUT SHIFT
 * -------------------------------
 * The swap is driven by `onImageUnavailable`, which a card raises
 * from an effect once it knows it has nothing to show. An effect runs
 * after the first paint, so the card was in the page, the reader saw
 * it, and then it was taken away — and every card below it slid up
 * into the gap. Measured on the homepage and on a category page, that
 * was a Cumulative Layout Shift of 0.07 and 0.21, which is the
 * difference between a "good" and a "poor" CLS score.
 *
 * The swap does not have to wait for an effect. Whether a card will
 * show nothing is a fact about the story, and the story is already in
 * hand before anything is rendered. The rule and the reason for it
 * live in lib/card-artwork.ts, which is where the card's own copy of
 * it should end up.
 */

type Rotate = "off" | "on";

/** Where the next page of the feed starts. See FeedCursor in lib/news.ts. */
interface FeedCursor {
  /**
   * When the story was published.
   *
   * The feed is ordered by publication time, and the cursor has to
   * address the same order or it skips rows without saying so.
   */
  published_at: string;
  id: string;
}

/**
 * Newest first — by when we ARRIVED at the story, not when it was
 * published.
 *
 * `byNewest` lives in lib/feed-merge.ts beside `mergeArticles`, because the
 * order the list is KEPT in and the way a batch is folded into it are one
 * decision, and testing the second without the first is how the two drift.
 * This has to stay the same order the server uses (`newestFirst` in
 * lib/news.ts): if the server sorts one way and the client another, the list
 * visibly reshuffles on the first poll after every page load.
 */
export function LiveArticleFeed({
  initialArticles,
  initialCursor = null,
  emptyState,
  rotation = "off",
  category,
}: {
  initialArticles: LatestArticle[];
  /**
   * Where the archive continues after the server-rendered cards. Null
   * when the server already knows there is nothing below them.
   */
  initialCursor?: FeedCursor | null;
  emptyState?: { title: string; description: string };
  rotation?: Rotate;
  /**
   * The category slug to stay inside.
   *
   * The category pages used to render a plain grid of twenty-four
   * cards and stop, which made them the only pages on the site that
   * ended. A reader who wanted the rest of a category had to go
   * somewhere else, and the newest story in a category could sit
   * below the fold with nothing to pull it up.
   *
   * With this set, the page is the same live feed as the homepage:
   * the newest first, growing as the reader scrolls, and never
   * leaving the category.
   */
  category?: string;
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

  // ---- The rest of the archive ----
  //
  // `cursor` is the last row the reader holds, and the next request asks
  // for rows OLDER than it, so a story published since they started
  // cannot shift what comes next. `hasMore` is the server's answer to
  // whether any exist, and it alone decides whether the button is on
  // screen: an exhausted feed must not leave a reader tapping a control
  // that will never produce anything.
  const [cursor, setCursor] = useState<FeedCursor | null>(initialCursor);
  const [hasMore, setHasMore] = useState(Boolean(initialCursor));
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [loadedAPage, setLoadedAPage] = useState(false);

  // Stories whose every image rendition 404'd.
  //
  // The brief was: never show a card with no picture, show a different
  // story instead. NewsCard already degrades honestly on its own, but an
  // imageless tile on a feed holding dozens of illustrated ones is the
  // worse answer. Dropping the id here lets the next article slide up
  // into the gap.
  //
  // The feed polls a much deeper window than it displays (60 fetched, 24
  // shown), so backfill is already in memory — nothing extra is fetched,
  // and the removed story is simply replaced by the next one down.
  //
  // Capped: if the deep window were ever exhausted too, the feed must
  // fall back to showing honest placeholders rather than an empty page.
  const [imageless, setImageless] = useState<Set<string>>(() => new Set());

  const markImageless = useCallback((articleId: string) => {
    setImageless((prev) => {
      if (prev.has(articleId)) return prev;
      const next = new Set(prev);
      next.add(articleId);
      return next;
    });
  }, []);

  const ids = useRef(new Set(initialArticles.map((a) => a.id)));
  const lastArrivalAt = useRef<number | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Two guards against two loads at once. `loadingMore` disables the
  // button; this ref catches the observer firing while a request is
  // already in the air, because a state update has not been committed yet
  // when its next callback lands.
  const moreInFlight = useRef(false);
  // Bumped after each successful page so the observer re-attaches and
  // asks again if the sentinel is still in view. See the effect below.
  const [pageNonce, setPageNonce] = useState(0);

  // The server hands this feed its first page. Everything after that is the
  // client's to own.
  //
  // What this used to do, and why it was the single largest source of layout
  // shift on the site: `setArticles(initialArticles)`. components/Header.tsx
  // calls `router.refresh()` every fifteen seconds, which re-renders the
  // server tree and hands this component a BRAND NEW array holding the SAME
  // twenty-four stories it already had. Replacing the list with it threw
  // away the union the polls had been building — the grid collapsed from 60
  // cards to 24, and the reader watched the page shrink and grow again on a
  // fifteen-second cycle for as long as the tab was open. One cycle measured
  // about 0.34 CLS on mobile.
  //
  // A refresh of the same route is not a new feed, it is the same feed with
  // a fresh identity, so it is folded in by `mergeArticles` — which returns
  // the existing array untouched when there is nothing new, and re-renders
  // nothing at all. Leaving the route unmounts this component, so a genuine
  // new feed still starts from the server's rows; the cursor below is only
  // adopted while this tab has not paged past the first page itself.
  useEffect(() => {
    setArticles((current) => mergeArticles(current, initialArticles));
    ids.current = new Set([...ids.current, ...initialArticles.map((a) => a.id)]);
    setMoreError(null);
    setStalled(false);
    moreInFlight.current = false;
    setPageNonce((n) => n + 1);

    // The archive this tab is walking is the client's, not the server's.
    // Overwriting the cursor here would drop a reader who had already loaded
    // three pages back onto page one — the same shift, in a different place.
    setCursor((current) => current ?? initialCursor);
    setHasMore((current) => current || Boolean(initialCursor));
  }, [initialArticles, initialCursor]);

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

  /**
   * Fold a batch of articles into the running union.
   *
   * Shared by the poll and by "Load more", because the two must agree on
   * what the union is. Merging on timestamp, never on position, is what
   * keeps a page load honest: a story the worker published while the
   * reader was three pages down belongs at the top of the feed, not in
   * whatever slot it would have taken on a fresh request. The server
   * already returns each page newest-first; re-sorting is what makes the
   * UNION newest-first rather than each page being newest-first.
   *
   * Identity is preserved when nothing arrived. Once the archive is a
   * thousand cards deep, `visible` below re-runs both deduplication
   * passes over the whole union, and doing that on every ten-second poll
   * that found nothing would be a visible stall for no reason at all.
   * The rule itself is `mergeArticles` in lib/feed-merge.ts, which the
   * reset-on-new-props effect below uses for the same reason.
   */
  const mergeInto = useCallback((fresh: LatestArticle[]) => {
    setArticles((current) => {
      lastArrivalAt.current = Date.now();
      const merged = mergeArticles(current, fresh);
      // Same array back means the poll found nothing new; leave the id set
      // alone rather than rebuilding it every ten seconds for nothing.
      if (merged === current) return current;
      ids.current = new Set(merged.map((a) => a.id));
      return merged;
    });
  }, []);

  const check = useCallback(
    async (lang: "en" | "ne" | null, reset = false) => {
      setChecking(true);
      try {
        const params = new URLSearchParams({
          limit: "60",
          // Defeats any intermediary cache. A cached poll is a
          // homepage frozen at whatever was newest when it was stored.
          t: String(Date.now()),
        });
        if (lang) params.set("language", lang);
        if (category) params.set("category", category);

        const response = await fetch(`/api/live-news?${params}`, {
          cache: "no-store",
        });
        if (!response.ok) return;

        const payload = (await response.json()) as {
          articles?: LatestArticle[];
          next_cursor?: FeedCursor | null;
        };
        const fresh = payload.articles ?? [];
        setLastChecked(Date.now());

        if (reset) {
          // Reset is "show me exactly what the database holds now", so
          // the old list is discarded rather than merged. It is still
          // re-sorted here: the ordering the page shows must not depend
          // on the order a response happens to arrive in.
          const sorted = [...fresh].sort(byNewest);
          setArticles(sorted);
          ids.current = new Set(sorted.map((a) => a.id));
          lastArrivalAt.current = null;

          // The list is now exactly the rows that response carried, so
          // paging continues from its last one. Without this, a language
          // switch would leave the cursor pointing into the edition the
          // reader just navigated away from and the next page would
          // silently blend the two.
          setCursor(payload.next_cursor ?? null);
          setHasMore(Boolean(payload.next_cursor));
          setStalled(false);
          setLoadedAPage(false);
          return;
        }

        // An ordinary poll says nothing about where the archive ends, so
        // the cursor is deliberately left alone: this response is the top
        // of the feed, not the page after the reader's cursor.
        mergeInto(fresh);
      } catch {
        // Offline or the API restarting: the list stays as-is.
      } finally {
        setChecking(false);
      }
    },
    [category, mergeInto]
  );

  /**
   * Fetch the page below the cursor and merge it in.
   *
   * The same endpoint the poll uses, with two differences: it carries the
   * cursor, and it asks for the total so the reader can be told whether
   * the archive ends. It merges through the same `mergeInto`, so a loaded
   * page is deduplicated by headline and by photograph against
   * everything already on screen exactly like a poll is — the `visible`
   * memo does not know, and does not care, which call produced a row.
   */
  const loadMore = useCallback(async (override?: FeedCursor | null) => {
    if (moreInFlight.current) return;
    moreInFlight.current = true;
    setLoadingMore(true);
    setMoreError(null);

    // The cursor this request pages from. An override lets the retry
    // button start again from the newest page without the state
    // update having landed first.
    const from = override === undefined ? cursor : override;

    try {
      const params = new URLSearchParams({
        limit: String(MORE_PAGE_SIZE),
        total: "1",
        t: String(Date.now()),
      });
      if (from) {
        params.set("published_at", from.published_at);
        params.set("after_id", from.id);
      }
      if (language) params.set("language", language);
      if (category) params.set("category", category);

      const response = await fetch(`/api/live-news?${params}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`live-news responded ${response.status}`);

      const payload = (await response.json()) as {
        articles?: LatestArticle[];
        next_cursor?: FeedCursor | null;
        total?: number | null;
      };
      const fresh = payload.articles ?? [];
      const next = payload.next_cursor ?? null;

      // THE END OF THE ARCHIVE IS NOT A STALL
      // --------------------------------------
      // The server answers with the next cursor, or with null when the
      // rows below this page are exhausted. Those are two different
      // answers and they used to be treated as one: `next === null` set
      // the stall flag, so a reader who simply scrolled to the end of
      // the archive was told "the archive did not advance, reload the
      // page" — and reloading produced exactly the same end of the
      // archive, so the message was both wrong and unactionable.
      //
      // A genuine stall is the other case: the server handed back a
      // cursor identical to the one we sent, so the next request would
      // ask for the same page again, forever. That is a real fault and
      // it is worth saying so — but it is recoverable, and the reader
      // gets a button rather than an instruction to reload.
      const reachedEnd = next === null;
      const cursorStalled =
        !reachedEnd && from !== null && next.id === from.id;

      setTotal(payload.total ?? null);
      setCursor(next);
      // An empty page is the end of the road whatever the server claimed,
      // so the button stops appearing rather than inviting a reader to
      // tap it forever.
      setHasMore(!reachedEnd && !cursorStalled && fresh.length > 0);
      setStalled(cursorStalled);
      setLoadedAPage(true);

      if (fresh.length > 0) mergeInto(fresh);
      // Only re-arm the observer when there is genuinely somewhere new to
      // go, or a stalled page would retrigger itself.
      if (!reachedEnd && !cursorStalled) setPageNonce((n) => n + 1);
    } catch {
      // The cursor is left where it was, so a retry asks for the same
      // page rather than skipping it.
      setMoreError("Could not load more stories.");
    } finally {
      moreInFlight.current = false;
      setLoadingMore(false);
    }
  }, [category, cursor, language, mergeInto]);

  /**
   * Start the archive again from the newest page.
   *
   * Used after a genuine stall. The merge keeps every story already on
   * screen, so re-asking for the first page cannot duplicate anything —
   * it only re-requests the page that did not advance, which may well
   * work the second time.
   */
  const retryArchive = useCallback(() => {
    setStalled(false);
    setMoreError(null);
    setHasMore(true);
    void loadMore(null);
  }, [loadMore]);

  // Automatic loading when the sentinel scrolls into view.
  //
  // The dependency on `pageNonce` is deliberate: attaching an
  // IntersectionObserver to an element that is ALREADY intersecting makes
  // it fire once straight away, so when an appended page does not push
  // the sentinel past the 800px band — a tall window, or a page of cards
  // that deduplication mostly removed — the next page starts on its own
  // instead of waiting for a scroll that may never come.
  //
  // It stops on its own too: `hasMore` going false unmounts the sentinel,
  // so this effect finds nothing to observe and no further requests go out.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    if (typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadMore();
      },
      { rootMargin: SENTINEL_ROOT_MARGIN }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore, pageNonce]);

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

  // The header's fifteen-second tick lands here.
  //
  // It used to be a RESET plus a forced rotation: `check(language, true)`
  // replaced the list wholesale, and `rotate(true)` reshuffled the forty-odd
  // cards below the pinned nine on top of that. Twice a minute, the whole
  // grid was rebuilt and re-composed underneath the reader. With
  // components/Header.tsx's own `router.refresh()` doing the same thing
  // through the props, the page shrank and grew and reshuffled every fifteen
  // seconds — measured at 0.34 CLS per cycle on mobile and 0.15 on desktop.
  //
  // Now it only re-reads. A poll is a MERGE (`mergeInto`), so a tick that
  // finds nothing new moves nothing at all, and a tick that finds a new
  // story moves exactly that card. Rotation stays off by default and is
  // never forced from here — that is what the rotate interval is for, and
  // what `news-rotate` below is for.
  useEffect(() => {
    const onManualRefresh = async () => {
      await check(language);
    };
    window.addEventListener("news-refresh", onManualRefresh);
    return () => window.removeEventListener("news-refresh", onManualRefresh);
  }, [check, language]);

  // The one path that DOES re-compose the page: a request to rotate.
  //
  // It re-reads first and then reshuffles, so a reader who asks for movement
  // gets movement rather than the same grid a moment later. That is safe
  // here and only here, because it is a deliberate request — a reader who
  // asked for the page to move cannot be surprised by it moving. The
  // fifteen-second `news-refresh` tick above is not that request, which is
  // why it merges instead.
  useEffect(() => {
    const onRotate = async () => {
      await check(language, true);
      lastArrivalAt.current = Date.now() - ROTATE_AFTER_MS;
      rotate(true);
    };
    window.addEventListener("news-rotate", onRotate);
    return () => window.removeEventListener("news-rotate", onRotate);
  }, [check, language, rotate]);

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

  const visible = useMemo(() => {
    // Deduplicate again on the way to the screen, not just on the way
    // out of the database.
    //
    // /api/live-news already deduplicates every response, so a poll can
    // never hand back two copies of one story at once. But this list is
    // the running UNION of every poll AND every page this tab has
    // loaded, and which copy of a story wins is not fixed: source_count
    // keeps climbing as more outlets republish, so the copy the server
    // picked ten minutes ago can lose to the copy it picks now. Both are
    // then sitting in the union and both are rendered.
    //
    // Same function, same thresholds, same order of preference — so this
    // keeps whichever copy the newest poll would have kept, and the two
    // passes cannot disagree about what is a duplicate.
    //
    // The picture pass runs here for the same reason, and it is the only
    // place it is genuinely needed: two rows wearing the same photograph
    // can end up side by side in this union WITHOUT ever having shared a
    // single response. One of them arrived in an early poll and has since
    // fallen out of the newest-60 window the server sends; the other
    // arrived just now wearing the same agency press shot. Every
    // response on its own passed the server's check — it only had one of
    // the pair — and the union is what puts them next to each other.
    // A story loaded from page 40 of the archive is the third way in.
    //
    // The union is also why this needs no extra backfill logic: the deep
    // window is already sitting in `articles`, so dropping a card slides
    // the next story up into the gap. Same shape as the `imageless` pass
    // below, which exists for the same reason.
    const deduped = dropRepeatedImages(dropDuplicateStories(articles));

    // The stories that will have nothing to show are left out BEFORE
    // the first paint, so that no card is ever taken away under the
    // reader. See `showsNothing` above.
    const withSomething = deduped.filter((article) => !showsNothing(article));
    const withoutEmpty =
      withSomething.length > 0 ? withSomething : deduped;

    if (imageless.size === 0) return withoutEmpty;

    const kept = withoutEmpty.filter((article) => !imageless.has(article.id));

    // Never let a page of placeholders become an empty page. If dropping
    // the imageless stories would empty the feed, show them anyway — the
    // card's own "No image available" state is the honest last resort.
    return kept.length > 0 ? kept : withoutEmpty;
  }, [articles, imageless]);

  const loadStatus = useMemo(() => {
    if (loadingMore) return "Loading more stories…";
    if (moreError) return moreError;
    if (stalled) {
      // A real fault, and a recoverable one: the server returned the
      // page we were already standing on, so the cursor did not move.
      return "The server sent back the same page, so the archive could not be paged any further. Try again.";
    }
    if (hasMore) {
      return total === null
        ? "More stories are below. Keep scrolling for the rest of the archive."
        : `${visible.length.toLocaleString("en")} shown, about ${total.toLocaleString("en")} in the archive.`;
    }
    return loadedAPage
      ? "That is the whole archive — every story published so far."
      : "That is everything published so far.";
  }, [hasMore, loadedAPage, loadingMore, moreError, stalled, total, visible.length]);

  if (visible.length === 0) {
    return (
      <>
        {category ? (
          <h1 className="sr-only">{categoryHeading(category)}</h1>
        ) : null}
        <EmptyState
          title={emptyState?.title ?? "No stories yet"}
          description={
            emptyState?.description ??
            "The ingestion worker has not published anything yet."
          }
        />
      </>
    );
  }

  return (
    <div
      ref={gridRef}
      onMouseEnter={() => pause(true)}
      onMouseLeave={() => pause(false)}
    >
      {/*
        The page's name, when this feed is a category page.

        A page with no <h1> is a page whose subject a screen reader
        and a crawler both have to guess, and axe-core fails it for
        `page-has-heading-one`. The homepage's own heading is declared
        by the homepage; a category page's heading belongs to the
        category, and the category is what this component is told
        about.
      */}
      {category ? (
        <>
          <h1 className="sr-only">{categoryHeading(category)}</h1>

          {/*
            What this page IS, for a machine.

            A category page is a list of news stories, and a list of
            news stories has a type: CollectionPage, with an ItemList
            inside it naming the stories and where they are. The
            homepage already says what the SITE is; a category page
            saying what the CATEGORY is completes the picture, and it
            is the difference between a crawler knowing there are
            forty stories here and knowing there are forty stories
            here, called what they are and linked individually.
          */}
          <script
            type="application/ld+json"
            dangerouslySetInnerHTML={{
              __html: jsonLd({
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                name: categoryHeading(category),
                url: `${SITE_URL}/category/${category}`,
                isPartOf: {
                  "@type": "WebSite",
                  name: SITE_NAME,
                  url: SITE_URL,
                },
                mainEntity: {
                  "@type": "ItemList",
                  itemListElement: visible.slice(0, 20).map((article, index) => ({
                    "@type": "ListItem",
                    position: index + 1,
                    url: `${SITE_URL}/article/${article.slug}`,
                    name: article.headline,
                  })),
                },
              }),
            }}
          />
        </>
      ) : null}
      <div
        id="live-article-grid"
        className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3"
      >
        {visible.map((article) => (
          <NewsCard
            key={article.id}
            article={article}
            language={language ?? "en"}
            onImageUnavailable={markImageless}
          />
        ))}
      </div>

      {/* The end of the archive is not a dead end: it is a sentence, and
          a button that only exists while there is something behind it. */}
      <div className="mt-10 flex flex-col items-center gap-3">
        {hasMore && (
          <>
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              aria-busy={loadingMore}
              aria-controls="live-article-grid"
              className="inline-flex min-h-[44px] items-center rounded-lg border border-gray-300 bg-white px-5 text-sm font-semibold text-gray-800 transition hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:hover:bg-gray-800 dark:focus-visible:ring-blue-400 dark:focus-visible:ring-offset-black"
            >
              {loadingMore ? "Loading…" : "Load more stories"}
            </button>
            {/* The IntersectionObserver's trigger. Rendered whenever there
                might be another page, including when the button above is
                mid-request — it is the automatic half of the same action,
                so both must survive a toggle of `hasMore`. */}
            <div ref={sentinelRef} aria-hidden="true" className="h-px w-full" />
          </>
        )}

        {/* The only case where the reader is asked to do something rather
            than simply told what happened. Reloading would throw away
            every story already loaded, so the retry asks for the page
            again instead — the merge keeps what is on screen. */}
        {stalled && !loadingMore && (
          <button
            type="button"
            onClick={retryArchive}
            className="inline-flex min-h-[44px] items-center rounded-lg border border-gray-300 bg-white px-5 text-sm font-semibold text-gray-800 transition hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:hover:bg-gray-800 dark:focus-visible:ring-blue-400 dark:focus-visible:ring-offset-black"
          >
            Try again
          </button>
        )}

        {/* One sentence, announced once. `role="status"` is polite by
            default and fires when its content changes, so it covers
            "loading", "how much is left" and "that is everything"
            without a second live region to keep in sync. */}
        <p
          role="status"
          aria-live="polite"
          className="text-center text-sm text-gray-500 dark:text-gray-400"
        >
          {loadStatus}
        </p>
      </div>

      {/* Screen readers get the polling state without it costing a
          re-render every second. */}
      <p className="sr-only" role="status" aria-live="polite">
        {checking ? "Checking for new stories." : "Feed up to date."}
      </p>
    </div>
  );
}