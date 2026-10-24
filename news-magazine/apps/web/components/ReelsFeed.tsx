"use client";

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import { useImagelessFilter } from "@/components/ImagelessGrid";
import { NewsCard, type NewsCardArticle } from "@/components/NewsCard";
import { ReelCard } from "@/components/ReelCard";
import { StoryDialog } from "@/components/StoryDialog";
import {
  DRIFT_PX_PER_SEC,
  driftOffsetAfter,
  isColumnHeld,
  isDriftHeld,
  shouldDriftRun,
} from "@/lib/drift";
import type { Reel } from "@/lib/reels";
import { SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";
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

/*
  Heights are `h-full`, not `h-[100dvh]`.

  The route makes the page exactly the viewport minus the header
  (app/category/[slug]/page.tsx), so anything that sized itself against
  the viewport again would be 64px too tall and would push a
  page-level scrollbar onto the right edge of the screen. `h-full`
  inherits the height the route already got right, and the middle
  column's own scroller then fills it exactly.

  `overflow-y-scroll` on the feed is deliberate and is NOT the scrollbar
  the reader complained about: that one is inside the middle column and
  is what makes the snap scroller look like a phone feed. The scrollbar
  that was on the right edge of the screen was the PAGE's, and this is
  what removes it.
*/
const FEED_SCROLL =
  "h-full min-h-0 w-full snap-y snap-mandatory overflow-y-scroll overscroll-contain scroll-smooth bg-gray-950";
const FEED_ITEM = "h-full w-full shrink-0 snap-start scroll-smooth";
const COLUMN_WINDOW = "h-full min-h-0 overflow-hidden";

/**
 * How many reels are mounted at once.
 *
 * THE CATALOGUE IS NOT THE PAGE
 * -----------------------------
 * The page is a video feed, and a video feed is a thing the reader
 * scrolls through one screen at a time. Rendering the whole catalogue
 * up front put every clip in the document twice — once for the phone
 * layout and once for the desktop one — which on the live site was:
 *
 *   166 reels x 2 layouts   332 reel cards
 *   + the drifting columns  ~48 news cards
 *                             ---
 *                             380 cards, 17,000 DOM nodes
 *
 * and the measured cost of that was a 37-second Total Blocking Time on
 * a throttled phone. Not 37 seconds of video: 37 seconds of the main
 * thread being told about four hundred cards nobody had scrolled to
 * yet.
 *
 * A window keeps the promise the feed makes — endless, and the whole
 * catalogue is reachable — without paying for the parts of it the
 * reader has not reached. The window grows as the reader approaches
 * its end, so the catalogue is still all there, and the reader's place
 * is never disturbed by it arriving.
 */
const REELS_WINDOW = 8;

/** How many more reels are mounted each time the reader nears the end. */
const REELS_WINDOW_STEP = 8;

/**
 * How far ahead of the end of the feed the next batch is mounted.
 *
 * The reader is never made to wait for a reel: the batch is ready
 * before they reach it.
 */
const REELS_WINDOW_ROOT_MARGIN = "800px 0px";

/**
 * The `inert` attribute, for the drifting columns' hidden repeats.
 *
 * React 18 does not know the attribute — it arrived in React 19 — and
 * the types that do know it declare it as a boolean, which React 18
 * would refuse to render (it drops `true` on an attribute it does not
 * recognise, with a warning). So it is passed as the STRING value the
 * content attribute takes: `inert` is a boolean content attribute, so
 * present means inert whatever its value, and `inert=""` is how HTML
 * spells "present".
 */
const INERT = { inert: "" } as unknown as React.HTMLAttributes<HTMLDivElement>;

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
  side,
  onOpenStory,
}: {
  articles: LatestArticle[];
  paused: boolean;
  reduceMotion: boolean;
  label: string;
  /**
   * Which side of the reels this column is on.
   *
   * A story opened from this column opens IN this column, and only
   * this column stops moving while it is open. The other column keeps
   * drifting and the reel keeps playing, so the page stays alive
   * around the story the reader is reading.
   */
  side: "left" | "right";
  /**
   * Open a story in place, over the feed, instead of navigating to it.
   *
   * The columns are the one place on the site where a headline is the
   * only way from the current page to a story, and following it used to
   * throw the reader out of the video they were watching.
   */
  onOpenStory?: (
    article: NewsCardArticle,
    trigger: HTMLElement,
    side: "left" | "right"
  ) => void;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const copyRef = useRef<HTMLDivElement | null>(null);
  const offsetRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const lastRef = useRef<number | null>(null);
  const hoverRef = useRef(false);
  const [copies, setCopies] = useState(2);

  /*
    The one place the "no image available" placeholder was most visible on
    the site. These columns used to render a bare <NewsCard article={...} />,
    so a story whose artwork was dead had nothing to ask to be replaced and
    the placeholder just stayed — and because the drift loop repeats this
    list 2-10 times, one such story was painted up to twenty times across
    the two columns.

    Filtering the source list ONCE, above the copies, is what makes this
    correct: drop inside `cardList` instead and each copy would report the
    same id back, and a dropped card would keep painting in every copy
    that had already been measured.

    Declared before the measurement effect below rather than next to the
    render, because that effect needs `visible` in its dependency list —
    dropping a card shortens the column, and the copy count has to be
    worked out again against the new height. It also has to sit above any
    early return, since the number of hooks a component calls must not
    change between renders; `sideArticles` is a server prop and normally
    never changes, but "normally" is not a guarantee and React throws
    rather than paper over it.
  */
  const { visible, markImageless } = useImagelessFilter(articles);

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
  }, [visible, reduceMotion]);

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
      const delta = now - previous;
      if (delta <= 0) return;

      /*
        The rules live in lib/drift.ts so they can be pinned by the test
        suite without a browser. The important one is the order: the
        column is only moved when it is allowed to move, so a held
        column keeps the offset it had — frozen exactly where it is,
        not reset — and it starts again from there when the hold ends.
      */
      if (
        !shouldDriftRun({
          held: paused,
          hovered: hoverRef.current,
          hidden: document.hidden,
        })
      ) {
        return;
      }

      const track = trackRef.current;
      const copyHeight = copyRef.current?.getBoundingClientRect().height ?? 0;
      if (!track || copyHeight <= 1) return;

      const next = driftOffsetAfter(
        offsetRef.current,
        delta,
        copyHeight,
        DRIFT_PX_PER_SEC
      );
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

  if (visible.length === 0) return null;

  const cardList = (duplicate: boolean) => (
    <div className="space-y-4">
      {visible.map((article) => (
        <NewsCard
          key={`${duplicate ? "copy" : "live"}-${article.id}`}
          article={article}
          onImageUnavailable={markImageless}
          // A headline in a drifting column opens the story over the
          // feed rather than navigating away from it, and opens it in
          // the column it was clicked in.
          onOpen={
            onOpenStory
              ? (article, trigger) => onOpenStory(article, trigger, side)
              : undefined
          }
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
              // `inert` is the other half of `aria-hidden`, and the
              // half that actually works.
              //
              // `aria-hidden` only removes a subtree from the
              // ACCESSIBILITY TREE. It does not take the links out of
              // the TAB ORDER, so a keyboard reader tabbed into a
              // hidden copy landed on a headline that was not on the
              // screen — and axe-core rightly failed the page for it
              // ("aria-hidden-focus: Focusable content should have
              // tabindex=-1 or be removed from the DOM").
              //
              // `inert` removes the subtree from the tab order AND from
              // the accessibility tree AND from the pointer, which is
              // what a decorative repeat of a list of links wants.
              //
              // React 18's types predate the attribute (it arrived in
              // React 19), so it is passed as the string value the
              // content attribute takes. `inert` is a boolean content
              // attribute: present means inert, whatever its value.
              {...(copy > 0 ? INERT : {})}
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

/**
 * THE FEED IS VIDEO ONLY
 * ----------------------
 * There is no picture card in this column any more.
 *
 * It used to be here: a story's own verified photograph, with a "News"
 * badge on it, in the same 9:16 frame as a clip. The operator's
 * complaint about it was unambiguous — "instead of showing reels videos
 * showing news pic fix that must show the middle reels must only show
 * the video" — and it was the right complaint: a still photograph in a
 * vertical video feed is a card the reader scrolls to, waits for, and
 * finds broken. The reels are in the side columns already, as the
 * news they are.
 *
 * So the feed is built from the clips alone (see lib/reels.ts), and a
 * clip that is one of our own stories is a clip like any other — with
 * the story's photograph as its poster and a link to the story on this
 * site.
 */

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
    How much of the catalogue is mounted. See REELS_WINDOW above.

    The window only ever GROWS. A new clip arriving (the header
    refreshes the server tree every so often) must not shrink the
    list under the reader, so the count is never lowered — the feed
    keeps whatever it has already mounted and adds to it.
  */
  const [windowSize, setWindowSize] = useState(REELS_WINDOW);

  /*
    THE ROTATION
    ------------
    The catalogue arrives newest-first, so every reader who opened the
    page saw the same clips, in the same order, in the same place -
    and the ones they had not scrolled to were the ones they never
    saw.

    A rotation fixes that: the catalogue starts at a random point and
    wraps around, so every fetched clip is reachable and two readers
    do not open the same page. The offset is chosen ONCE per visit
    and kept in a ref, so the order never changes under the reader
    when the server tree refreshes underneath it.
  */
  const rotation = useRef<number | null>(null);
  if (rotation.current === null) {
    rotation.current =
      reels.length > 0 ? Math.floor(Math.random() * reels.length) : 0;
  }

  /*
    THE NEWEST CLIPS ARE ALWAYS AT THE HEAD
    -----------------------------------------
    The rotation used to decide the whole order, and it starts at a
    random point in the catalogue - so the clips the reader had come
    for, the ones the pipeline had just fetched, were usually not on
    the screen at all. The reader's complaint was "the latest reels
    are missing on the UI", and it was true: with 245 clips and a
    random offset, the chance of the newest one being on the first
    screen was eight in two hundred and forty-five.

    So the head of the feed is the newest clips, always, and the
    rotation moves through the REST of the catalogue. Two readers
    still see different clips, and the newest is still on the screen.
  */
  const mountedReels = useMemo(() => {
    const count = Math.max(REELS_WINDOW, windowSize);
    const head = reels.slice(0, count);
    const rest = reels.slice(count);
    if (rest.length === 0) return head;
    const offset = rotation.current ?? 0;
    const rotatedRest =
      [...rest.slice(offset), ...rest.slice(0, offset)];
    return [...head, ...rotatedRest];
  }, [reels, windowSize]);

  /*
    The story the reader has open, if any.

    Clicking a headline in one of the drifting columns opens the story
    HERE, over the feed, instead of navigating to it: the reels stay
    mounted behind it, the reader keeps their place in the video, and
    the columns hold still while they read. Closing it hands the
    reader back to the card they clicked.

    The trigger is kept separately from the story because the story is
    data and the trigger is a DOM node, and the focus has to go back
    to the exact card that was clicked.
  */
  const [openStory, setOpenStory] = useState<NewsCardArticle | null>(null);
  /**
   * Which column the open story belongs to.
   *
   * The story opens IN its column, and only its column stops moving.
   * The other column keeps drifting and the reel keeps playing, so
   * the page stays alive around the story rather than going quiet
   * behind a modal.
   */
  const [storySide, setStorySide] = useState<"left" | "right" | null>(null);
  const storyTriggerRef = useRef<HTMLElement | null>(null);
  const backgroundRef = useRef<HTMLDivElement | null>(null);

  const openStoryInPlace = useCallback(
    (
      article: NewsCardArticle,
      trigger: HTMLElement,
      side: "left" | "right"
    ) => {
      storyTriggerRef.current = trigger;
      setStorySide(side);
      setOpenStory(article);
    },
    []
  );

  const closeStory = useCallback(() => {
    setOpenStory(null);
  }, []);

  /*
    Sound is a session-level choice, so the state lives here rather than
    inside each ReelCard. Tapping the speaker once now carries through
    every reel the reader swipes to afterwards, instead of resetting on
    each one.
  */
  const [muted, setMuted] = useState(true);

  const router = useRouter();

  /*
    Re-render when the header's poll says the world moved on.

    This is the whole reason the reels page ever changes. `ReelsFeed`
    receives its reels as props from a server component, so it renders
    whatever was in the database when the page was requested — and it
    previously never asked again. The header already broadcasts a
    `news-refresh` CustomEvent every 15 seconds (and immediately on a
    manual refresh); LiveArticleFeed listens for it and merges the newest
    page into what it already has, and ReelsFeed listens for it too, so
    a clip the worker has just stored appears without the reader
    reloading the page.

    `router.refresh()` rather than a fetch of our own: the reels are read
    server-side in `getReels()` and there is no reels API endpoint to poll.
    Re-rendering the server tree is the only way to see a new row, and it
    is the same mechanism the refresh button already uses.

    The read behind it is cached for fifteen seconds
    (`REELS_READ_REVALIDATE_SECONDS`), so a new clip lands within half a
    minute of the worker storing it.

    Scroll position is preserved across a refresh, so a reader who is
    three reels down is not thrown back to the top when a new clip lands.

    The reels list only ever GROWS by ingestion — `ingest_reels` upserts
    on (source_key, external_id) rather than deleting — so there is no
    case where the reader's place in the feed is invalidated underneath
    them and a reset would be warranted.
  */
  useEffect(() => {
    const refresh = () => router.refresh();
    window.addEventListener("news-refresh", refresh);
    return () => window.removeEventListener("news-refresh", refresh);
  }, [router]);

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

  /*
    THE FEED ROTATES
    ----------------
    The catalogue is finite — it is whatever the worker has stored, and
    for Wikimedia that is a couple of hundred Nepal clips — but the
    feed does not end. When the reader reaches the last clip, the feed
    goes back to the first one, so the page is endless in the way a
    playlist is: you can keep scrolling and you keep seeing clips, and
    the ones you have already watched come round again.

    A feed that simply STOPS is a page that says "you have reached the
    end", and on a vertical video feed that is an invitation to leave.
    The rotation is the difference between a feed and a list.

    The dwell matters. The wrap happens a couple of seconds after the
    last clip becomes the active one, not the instant it does: the
    reader has to be given time to see the clip they scrolled to
    before the page takes them back to the top.

    Both layouts are reset, because both exist in the DOM and the
    reader is looking at one of them.
  */
  const feedRefs = useRef(new Set<HTMLDivElement>());
  const lastReelId = reels.length ? reels[reels.length - 1]?.id : null;

  /*
    Mounting more of the catalogue.

    The window grows when the reader reaches the end of what is
    mounted. The feed is a snap scroller, so "reaches the end" is a
    scroll position rather than an element in view — and the check is
    made against the scroller itself, which is also how the reader's
    own gesture is what triggers it. Nothing is loaded in the
    background: a reel is mounted when the reader is a screen away
    from needing it.

    The two layouts are both in the DOM and only one of them has a
    height; the other measures zero and is skipped, so the window
    grows once, not twice.
  */
  const growthPending = useRef(false);

  useEffect(() => {
    const containers = [...feedRefs.current];
    if (containers.length === 0) return;

    const onScroll = () => {
      if (growthPending.current) return;

      for (const container of containers) {
        if (!container || container.clientHeight === 0) continue;
        const remaining =
          container.scrollHeight - container.scrollTop - container.clientHeight;
        if (remaining > 800) continue;

        growthPending.current = true;
        setWindowSize((current) =>
          current >= reels.length
            ? current
            : Math.min(reels.length, current + REELS_WINDOW_STEP)
        );
        break;
      }
    };

    for (const container of containers) {
      container.addEventListener("scroll", onScroll, { passive: true });
    }
    return () => {
      for (const container of containers) {
        container.removeEventListener("scroll", onScroll);
      }
    };
  }, [reels.length]);

  // A new batch has been mounted, so the next one may be requested.
  useEffect(() => {
    growthPending.current = false;
  }, [mountedReels.length]);

  useEffect(() => {
    if (!lastReelId || activeId !== lastReelId) return;

    const timer = window.setTimeout(() => {
      for (const container of feedRefs.current) {
        container?.scrollTo({ top: 0, behavior: "auto" });
      }
    }, 2500);

    return () => window.clearTimeout(timer);
  }, [activeId, lastReelId]);

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
    for (const reel of mountedReels) {
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
  }, [mountedReels]);

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

  /*
    `mountEmbed` follows the same rule `active` does, and for a much
    heavier reason.

    Both layouts exist in the DOM at once, so every embedded reel would
    otherwise instantiate its player TWICE — twenty-four YouTube IFrame
    Players on a twelve-card page. That is not a duplicate <video> that
    quietly decodes in the background; each one is a separate third-party
    player pulling its own script and session, and enough of them will
    stall the page and get this host throttled by Google.

    So exactly one embed exists on the page: the active clip, in the
    layout that is actually on screen. Every other embedded card shows
    its poster until it becomes the active one. The cost is that the
    player is fetched as the reader arrives at a clip rather than ahead
    of them, which is the same trade YouTube's own site makes and is
    invisible against a poster.
  */
  const renderFeed = (key: "mobile" | "desktop") => (
    <div
      key={key}
      ref={(element) => {
        // Registered so the rotation can send both layouts back to the
        // top. The inactive one has no height, but it is still in the
        // DOM and the reader will be looking at it after a resize.
        if (element) feedRefs.current.add(element);
        else feedRefs.current.delete(element as unknown as HTMLDivElement);
      }}
      className={`${FEED_SCROLL} ${key === "mobile" ? "lg:hidden" : "hidden lg:block"}`}
      // Keyboard paging between reels, so the feed is not mouse-only.
      tabIndex={0}
      aria-label="Reels"
    >
      {mountedReels.map((reel) => (
        <div
          key={reel.id}
          data-reel-id={reel.id}
          ref={attach.get(reel.id)}
          className={FEED_ITEM}
        >
          {/*
            Every card in the middle column is a clip. There is no
            picture card: the feed is built from the video rows alone
            (see lib/reels.ts), so a card here always has something to
            play.
          */}
          <ReelCard
            reel={reel}
            active={activeId === reel.id && layoutVisible === key}
            mountEmbed={activeId === reel.id && layoutVisible === key}
            muted={muted}
            onMutedChange={setMuted}
          />
        </div>
      ))}
    </div>
  );

  /*
    Whether the columns are being held still.

    Two reasons, and the second is the important one: the reader may
    have pressed the pause button, and a story may be open in front of
    the columns. A column that keeps drifting under a paragraph of
    text moves the words out from under the reader's eyes, so an open
    story holds the columns exactly where they are — and lets them
    carry on from there the moment the story closes.
  */
  const driftHeld = isDriftHeld({
    manuallyPaused: driftPaused,
    storyOpen: openStory !== null,
    reduceMotion,
  });

  /**
   * Whether a particular column is held still.
   *
   * Only the column the story was opened in stops. The other one
   * keeps drifting, and so do the reels - the reader is reading one
   * story, and the rest of the page carries on around them.
   */
  const columnHeld = useCallback(
    (side: "left" | "right") =>
      isColumnHeld(
        {
          manuallyPaused: driftPaused,
          storyOpen: openStory !== null,
          reduceMotion,
        },
        side,
        storySide
      ),
    [driftPaused, openStory, reduceMotion, storySide]
  );

  return (
    /*
      `h-full` because both layouts inside are `h-full`, and a
      percentage height against an auto-height parent resolves to
      auto. The route gives this the viewport minus the header, and
      this passes it down unchanged.
    */
    <div className="relative h-full">
      {/*
        The page's name.

        The reels page is a full-viewport video experience with no
        visible title, and a page with no <h1> is a page whose subject
        a screen reader and a crawler both have to guess. It is
        visually hidden rather than absent: the reader sees the video,
        the assistive technology is told what the video is.
      */}
      <h1 className="sr-only">Reels — short video from around Nepal</h1>

      {/*
        What this page IS, for a machine.

        A feed of short videos is a list of VideoObjects, and saying
        so is what lets a search engine, a reader's app or an agent
        know that the things on this page are videos — with a frame to
        show for each one, a title, and a date — rather than having to
        work that out from the markup.
      */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: jsonLd({
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            name: "Reels",
            url: `${SITE_URL}/category/reels`,
            isPartOf: {
              "@type": "WebSite",
              name: SITE_NAME,
              url: SITE_URL,
            },
            mainEntity: {
              "@type": "ItemList",
              itemListElement: mountedReels.map((reel, index) => ({
                "@type": "ListItem",
                position: index + 1,
                /*
                  Every card in this column is a clip — the feed is
                  video only — so every one of them is a VideoObject.
                  There is no picture card to describe as an image, and
                  calling one a VideoObject would put a photograph in
                  `contentUrl`, which is a claim about a file that does
                  not exist.
                */
                item: {
                  "@type": "VideoObject",
                  name: reel.title ?? "Reel",
                  description: reel.description ?? undefined,
                  thumbnailUrl: reel.poster_url ?? undefined,
                  uploadDate: reel.published_at ?? undefined,
                  contentUrl: reel.video_url,
                },
              })),
            },
          }),
        }}
      />

      {/*
        Everything the reader is not currently reading.

        It is one element so that a story can take the whole page out
        of the accessibility tree in one move: while the story is
        open, a screen reader must not be able to walk into the reels
        or the columns behind it, because the story is modal.

        The attribute is applied by the dialog rather than declared
        here, because Chrome refuses to hide a subtree that still holds
        the focus — and the focus starts on one of the cards inside
        this element. The dialog moves the focus into itself first and
        hides the background second.
      */}
      <div ref={backgroundRef} className="relative h-full">
        {/* ================= MOBILE ================= */}
        {renderFeed("mobile")}

        {/* ================= DESKTOP ================= */}
        <div className="hidden lg:grid lg:h-full lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:items-stretch lg:gap-6 lg:px-6 lg:py-4">
          <DriftingColumn
            articles={leftArticles}
            paused={columnHeld("left")}
            reduceMotion={reduceMotion}
            label="Latest news"
            side="left"
            onOpenStory={openStoryInPlace}
          />

          {/* The focused element. Everything else is scaffolding.

              `h-full` is what lets the feed inside resolve against the
              grid row rather than against the viewport. Without it the
              middle column is auto-height and the snap scroller has
              nothing to scroll within. */}
          <div className="mx-auto h-full min-h-0 w-full max-w-[420px]">{renderFeed("desktop")}</div>

          <DriftingColumn
            articles={rightArticles}
            paused={columnHeld("right")}
            reduceMotion={reduceMotion}
            label="Latest news"
            side="right"
            onOpenStory={openStoryInPlace}
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
            // `text-gray-600`, not `text-gray-500`: the button is 12px,
            // and at that size the WCAG AA threshold is 4.5:1 rather than
            // the 3:1 that large text gets. Grey-500 on the white/90
            // pill measured 3.87:1 — a real failure, caught by axe-core
            // as `color-contrast`. Grey-600 measures 6.05:1 over the
            // same composite.
            //
            // 48 pixels tall, like the other controls: it is a button a
            // finger has to hit, and it used to be 32.
            className="absolute bottom-4 left-1/2 hidden min-h-[48px] -translate-x-1/2 items-center rounded-full border border-gray-200 bg-white/90 px-3 text-xs font-medium uppercase tracking-wider text-gray-600 shadow-sm backdrop-blur transition-colors hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 lg:inline-flex"
          >
            {driftPaused ? "Resume news" : "Pause news"}
          </button>
        )}
      </div>

      {/*
        The story, over the feed.

        The reels are still mounted behind it — this is an overlay on
        the same page, not a navigation — and the columns are held
        still for as long as it is up.
      */}
      {openStory && (
        <StoryDialog
          article={openStory}
          side={storySide}
          onClose={closeStory}
          returnFocusTo={storyTriggerRef.current}
          background={backgroundRef.current}
        />
      )}
    </div>
  );
}