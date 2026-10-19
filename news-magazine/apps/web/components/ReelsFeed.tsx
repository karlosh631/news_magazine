"use client";

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { NewsCard } from "@/components/NewsCard";
import { ReelCard } from "@/components/ReelCard";
import type { Reel } from "@/lib/reels";
import type { LatestArticle } from "@/types/database";

/**
 * The vertical video feed.
 *
 * Two presentations of the same reels:
 *
 *   MOBILE (below lg) — one reel per screen, snapped. `dvh`, not `vh`:
 *   mobile browser chrome (the URL bar, the home indicator) is not part
 *   of `vh`, so a `100vh` feed is either taller than the visible area —
 *   the last card is cut off and cannot be snapped to — or has to be
 *   guessed at with padding. `dvh` tracks the real viewport.
 *
 *   DESKTOP (lg+) — three columns. News drifts slowly down the left and
 *   right, the reels hold the centre and stay about 420px wide. The
 *   drifting columns are borrowed wholesale from Google Discover and
 *   Snap: enough ambient motion that the page feels alive, slow enough
 *   that nothing competes with the reel. The rules that keep it tasteful
 *   rather than distracting are all in AutoScrollColumn below —
 *   reduced-motion readers get plain static lists, and the drift stops
 *   the moment the reader touches it or looks away.
 *
 * Exactly one video plays at a time. An IntersectionObserver narrows the
 * viewport to a thin band across its middle; the reel crossing that band
 * is the focused one and is the only one ReelCard is told to play. The
 * alternative — letting every reel autoplay — means N decoders competing
 * for the same CPU, and on a phone that is the difference between
 * smooth and unusable.
 */

/** How fast the side columns drift, in CSS pixels per second. */
const DRIFT_PX_PER_SEC = 10;

/**
 * Shared geometry between the mobile feed and the desktop centre column.
 *
 * The desktop grid is inset by 1rem top and bottom, so the centre column
 * is `100dvh` minus that. It has to be the same expression on the
 * scroller AND on every item, or snap points land in the wrong place and
 * one reel shows a slice of the next.
 */
/**
 * Which of the two feed layouts is actually on screen.
 *
 * `lg` is the same breakpoint Tailwind hides one copy at, so this and the
 * `lg:hidden` / `hidden lg:block` classes can never disagree.
 *
 * Starts as "mobile" so the server render and the first client render
 * agree — the alternative is a hydration mismatch.
 */
function useLayoutVariant(): "mobile" | "desktop" {
  const [variant, setVariant] = useState<"mobile" | "desktop">("mobile");

  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const apply = () => setVariant(query.matches ? "desktop" : "mobile");
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  return variant;
}

const FEED_SCROLL =
  "h-[100dvh] lg:h-[calc(100dvh-2rem)] w-full snap-y snap-mandatory overflow-y-scroll overscroll-contain scroll-smooth bg-gray-950";
const FEED_ITEM =
  "h-[100dvh] lg:h-[calc(100dvh-2rem)] w-full shrink-0 snap-start scroll-smooth";
const COLUMN_WINDOW = "h-[100dvh] lg:h-[calc(100dvh-2rem)] overflow-hidden";

/**
 * One news column that drifts downwards forever.
 *
 * Implemented as a single track translated on the Y axis and wrapped
 * modulo the height of ONE copy of the list, rather than by writing
 * `scrollTop`. Two reasons:
 *
 *   1. A transform does not trigger layout, so the drift costs a
 *      compositor frame rather than a full re-flow of dozens of cards
 *      sixty times a second.
 *   2. The track holds several identical copies of the list, so when the
 *      offset wraps there is always content under the window — the loop
 *      is invisible. With `scrollTop` you would need a scroll listener
 *      firing on every frame to catch the wrap before a blank strip
 *      appeared.
 *
 * `aria-hidden` on the copies past the first keeps a screen reader from
 * announcing the same headline three times. It does not remove their
 * links from the tab order — NewsCard owns those and is shared with the
 * rest of the site — but the copies are never on screen, and a
 * keyboard user reaching one lands on the identical article one copy
 * earlier in the same column.
 */
function AutoScrollColumn({
  articles,
  paused,
  reduceMotion,
  label,
}: {
  articles: LatestArticle[];
  paused: boolean;
  reduceMotion: boolean;
  label: string;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const copyRef = useRef<HTMLDivElement | null>(null);
  const offsetRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const lastRef = useRef<number | null>(null);
  const hoverRef = useRef(false);
  const [copies, setCopies] = useState(2);

  /**
   * How many copies are needed to cover roughly two viewports.
   *
   * One viewport of cover is the bare minimum (the loop is seamless as
   * soon as the track never runs out); two is headroom for a short list
   * and for the window being resized larger than it was measured at.
   * Capped so a feed with only one story cannot mount fifty cards.
   */
  useEffect(() => {
    if (reduceMotion) return;
    const measure = () => {
      const height = copyRef.current?.getBoundingClientRect().height ?? 0;
      if (height <= 0) return;
      const viewport = window.innerHeight || 800;
      setCopies(
        Math.min(10, Math.max(2, Math.ceil((viewport * 2) / height) + 1))
      );
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [articles, reduceMotion]);

  // The drift itself.
  useEffect(() => {
    // Reduced motion is not "drift slower" — it is no drift at all, and
    // the column renders as an ordinary static list below.
    if (reduceMotion) return;

    const step = (now: number) => {
      frameRef.current = requestAnimationFrame(step);

      const previous = lastRef.current ?? now;
      lastRef.current = now;
      // A tab that was throttled or a frame that was janked hands us a
      // delta of seconds. At 10px/s that is a visible jump, and for a
      // track that has to wrap cleanly, a jump straight past the seam.
      const delta = Math.min(100, now - previous);
      if (delta <= 0) return;

      if (hoverRef.current || paused || document.hidden) return;

      const track = trackRef.current;
      const copyHeight = copyRef.current?.getBoundingClientRect().height ?? 0;
      if (!track || copyHeight <= 1) return;

      let next = offsetRef.current + (DRIFT_PX_PER_SEC * delta) / 1000;
      // One full copy of travel and we are back where we started looking,
      // because the copy below is identical to the copy above.
      if (next >= copyHeight) next -= copyHeight;
      offsetRef.current = next;
      track.style.transform = `translate3d(0, ${-next}px, 0)`;
    };

    const start = () => {
      if (frameRef.current !== null) return;
      lastRef.current = null;
      frameRef.current = requestAnimationFrame(step);
    };
    const stop = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      lastRef.current = null;
    };

    // Nothing should move behind a tab nobody is looking at. Browsers
    // throttle rAF in a hidden tab anyway; this stops it outright and,
    // more importantly, makes "paused" a property of the listener rather
    // than of whatever the browser happens to do with a hidden tab.
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };

    document.addEventListener("visibilitychange", onVisibility);
    start();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [paused, reduceMotion]);

  if (articles.length === 0) return null;

  const cardList = (duplicate: boolean) => (
    <div className="space-y-4">
      {articles.map((article) => (
        <NewsCard
          key={`${duplicate ? "copy" : "live"}-${article.id}`}
          article={article}
        />
      ))}
    </div>
  );

  return (
    <div
      aria-label={label}
      className={COLUMN_WINDOW}
      // Hovering stops the drift. Grabbing a headline to read it is not
      // the moment to have the floor move under it.
      onPointerEnter={() => {
        hoverRef.current = true;
      }}
      onPointerLeave={() => {
        hoverRef.current = false;
      }}
      // Keyboard focus pauses for the same reason hover does.
      onFocusCapture={() => {
        hoverRef.current = true;
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          hoverRef.current = false;
        }
      }}
    >
      {reduceMotion ? (
        cardList(false)
      ) : (
        <div ref={trackRef} className="will-change-transform">
          {Array.from({ length: copies }, (_, copy) => (
            <div
              key={copy}
              ref={copy === 0 ? copyRef : undefined}
              aria-hidden={copy > 0}
              // The bottom padding is the gap between this copy's last
              // card and the next copy's first one, so it is part of the
              // measured height and the wrap stays seamless.
              className="pb-5"
            >
              {cardList(copy > 0)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Memoised, because the props are all stable and the alternative is bad:
 * `activeId` changes on every snap, which re-renders this component, which
 * re-renders fifty NewsCards that did not change — on a phone, on the
 * one interaction the reader is actually making.
 */
const DriftingColumn = memo(AutoScrollColumn);

export function ReelsFeed({
  reels,
  sideArticles,
}: {
  reels: Reel[];
  sideArticles: LatestArticle[];
}) {
  const [activeId, setActiveId] = useState<string | null>(
    reels[0]?.id ?? null
  );
  const [driftPaused, setDriftPaused] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);

  /*
    Sound is a session-level choice, so the state lives here rather than
    inside each ReelCard. Tapping the speaker once now carries through
    every reel the reader swipes to afterwards, instead of resetting on
    each one.
  */
  const [muted, setMuted] = useState(true);

  /**
   * Every rendered reel item — both copies of it.
   *
   * Not keyed by reel id: the mobile and desktop feeds render the same
   * reels, and a map keyed by id would keep only whichever one attached
   * last. On a phone that means the observed items are the ones inside
   * the `lg:hidden` container, which have no height and never intersect,
   * so nothing would ever play.
   */
  const itemRefs = useRef(new Set<HTMLElement>());

  // Respect the OS "reduce motion" setting. Perpetual scrolling is one of
  // the things it exists to switch off, so the columns become ordinary
  // lists and the pause control disappears with the motion it controls.
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduceMotion(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  // Stable ref callbacks. An inline arrow would detach and re-attach on
  // every render — including the render caused by `activeId` changing —
  // which empties the set the observer is mid-way through reading.
  const attach = useMemo(() => {
    const map = new Map<string, (element: HTMLElement | null) => void>();
    for (const reel of reels) {
      map.set(reel.id, (element) => {
        if (element) {
          itemRefs.current.add(element);
          return;
        }
        // React detaches a ref by calling it with null, without saying
        // which element it was holding. Drop everything registered for
        // this reel — there are at most two, one per breakpoint.
        itemRefs.current.forEach((registered) => {
          if (registered.dataset.reelId === reel.id) {
            itemRefs.current.delete(registered);
          }
        });
      });
    }
    return map;
  }, [reels]);

  /** Whichever on-screen reel is closest to the middle of the viewport. */
  const recompute = useCallback(() => {
    // Nothing plays behind a tab nobody is looking at.
    if (document.hidden) {
      setActiveId(null);
      return;
    }

    const middle = window.innerHeight / 2;
    let winner: string | null = null;
    let best = Infinity;

    itemRefs.current.forEach((element) => {
      const id = element.dataset.reelId;
      if (!id) return;
      const rect = element.getBoundingClientRect();
      // The feed for the breakpoint we are NOT on collapses to a zero
      // rect. It must never win.
      if (rect.height === 0) return;
      // Nor can a reel the reader has scrolled past or not yet reached.
      if (rect.bottom <= 0 || rect.top >= window.innerHeight) return;

      const distance = Math.abs((rect.top + rect.bottom) / 2 - middle);
      if (distance < best) {
        best = distance;
        winner = id;
      }
    });

    setActiveId((current) => (current === winner ? current : winner));
  }, []);

  // Keep the focused reel sensible if the list itself changes.
  useEffect(() => {
    setActiveId((current) =>
      current && reels.some((reel) => reel.id === current)
        ? current
        : reels[0]?.id ?? null
    );
  }, [reels]);

  useEffect(() => {
    const targets: HTMLElement[] = [];
    itemRefs.current.forEach((element) => targets.push(element));
    if (targets.length === 0) return;

    const observer = new IntersectionObserver(
      () => {
        // The observer is the trigger, not the answer. It tells us
        // "something crossed the middle of the screen"; the answer is
        // whichever reel is nearest that middle right now, read fresh
        // because the callback may fire for a reel that has already
        // scrolled past by the time it runs.
        recompute();
      },
      // A thin band across the middle of the viewport. "Intersecting"
      // therefore means "crossing the centre", which is exactly the
      // definition of the focused reel — no threshold tuning, and a
      // mid-scroll frame hands over to the incoming reel promptly.
      // It also means the work is done on the handful of frames where a
      // reel crosses the middle, not on every scroll event.
      { rootMargin: "-45% 0px -45% 0px", threshold: 0 }
    );

    targets.forEach((element) => observer.observe(element));

    // Off screen means nothing plays. A reel scrolled past the edge of
    // the viewport keeps decoding otherwise, which on a phone is the
    // single most expensive thing this page can do.
    const onVisibility = () => recompute();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [attach, recompute]);

  // Split the news down the middle rather than interleaving it, so each
  // column still reads newest-first on its own. Memoised so the arrays
  // keep their identity: a fresh `.slice()` on every render would defeat
  // `memo` below and re-measure the columns on every snap.
  const { leftArticles, rightArticles } = useMemo(() => {
    const midpoint = Math.ceil(sideArticles.length / 2);
    return {
      leftArticles: sideArticles.slice(0, midpoint),
      rightArticles: sideArticles.slice(midpoint),
    };
  }, [sideArticles]);

  /*
    Both layouts are rendered at once and hidden with Tailwind, because
    the mobile feed is a snap scroller and the desktop one is inset into a
    three-column grid — they are genuinely different markup, not the same
    markup at two sizes.

    That means every reel exists TWICE in the DOM, and both copies carry
    the same `reel.id`. Comparing `activeId === reel.id` therefore marks
    BOTH copies active, and both call play(). A display:none video cannot
    be seen but can still emit sound, so with sound enabled the reader
    heard every reel twice.

    Only the layout actually on screen is allowed to become active.
  */
  const layoutVisible = useLayoutVariant();

  const renderFeed = (key: "mobile" | "desktop") => (
    <div
      key={key}
      className={`${FEED_SCROLL} ${key === "mobile" ? "lg:hidden" : "hidden lg:block"}`}
      // Keyboard paging between reels, so the feed is not mouse-only.
      tabIndex={0}
      aria-label="Reels"
    >
      {reels.map((reel) => (
        <div
          key={reel.id}
          data-reel-id={reel.id}
          ref={attach.get(reel.id)}
          className={FEED_ITEM}
        >
          <ReelCard
            reel={reel}
            active={activeId === reel.id && layoutVisible === key}
            muted={muted}
            onMutedChange={setMuted}
          />
        </div>
      ))}
    </div>
  );

  return (
    <div className="relative">
      {/* ================= MOBILE ================= */}
      {renderFeed("mobile")}

      {/* ================= DESKTOP ================= */}
      <div className="hidden lg:grid lg:h-[100dvh] lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)_minmax(0,1fr)] lg:items-start lg:gap-6 lg:px-6 lg:py-4">
        <DriftingColumn
          articles={leftArticles}
          paused={driftPaused}
          reduceMotion={reduceMotion}
          label="Latest news"
        />

        {/* The focused element. Everything else is scaffolding. */}
        <div className="mx-auto w-full max-w-[420px]">{renderFeed("desktop")}</div>

        <DriftingColumn
          articles={rightArticles}
          paused={driftPaused}
          reduceMotion={reduceMotion}
          label="Latest news"
        />
      </div>

      {/* Ambient motion has to be stoppable, not just interruptible. */}
      {!reduceMotion && (
        <button
          type="button"
          onClick={() => setDriftPaused((value) => !value)}
          // A stable name plus a pressed state: a button whose own label
          // flips between "pause" and "resume" reads as two different
          // controls to a screen reader.
          aria-label="Auto-scrolling news columns"
          aria-pressed={driftPaused}
          className="absolute bottom-4 left-1/2 hidden -translate-x-1/2 rounded-full border border-gray-200 bg-white/90 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500 shadow-sm backdrop-blur transition-colors hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 lg:inline-flex"
        >
          {driftPaused ? "Resume news" : "Pause news"}
        </button>
      )}
    </div>
  );
}