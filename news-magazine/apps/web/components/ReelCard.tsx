"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SafeImage } from "@/components/SafeImage";
import type { Reel } from "@/lib/reels";
import { SITE_URL } from "@/lib/site";

/**
 * One vertical reel, with its credit always reachable.
 *
 * The licence on most of this footage requires the creator and the licence
 * to be shown, so the <details> block below is NOT conditional on anything.
 * It is not a flag, not a hover affordance and not a "tap the poster to
 * find the credit" gesture — hover does not exist on a phone, and a
 * disclosure that hides its own contents behind an interaction is a
 * disclosure that never gets opened. The credit is in the DOM on every
 * render; it is simply not the loudest thing in the frame. That is the
 * whole design.
 */

/**
 * Where a shared link should point.
 *
 * There is no per-reel route yet — `reels` is a category slug and the page
 * at /category/reels renders the feed. Sharing the feed is the only link
 * guaranteed to resolve; if a reel page is added later this is the single
 * line that changes.
 */
function canonicalUrl(): string {
  return `${SITE_URL}/category/reels`;
}

function formatCount(n: number): string {
  return n.toLocaleString();
}

/** Seconds as m:ss, the way a clip length reads. 0 is not a duration. */
function formatDuration(seconds: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null;
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ReelCard({
  reel,
  active = false,
  muted: mutedProp,
  onMutedChange,
}: {
  reel: Reel;
  active?: boolean;
  /**
   * Sound state, owned by the feed.
   *
   * It used to live here, which meant every reel in the feed started
   * muted again: a reader who tapped the speaker once then swiped to
   * the next clip found it silent, and had to tap again on every single
   * reel for the rest of the session. Sound is a property of the
   * session, not of one clip.
   *
   * Uncontrolled when omitted, so the component still works alone.
   */
  muted?: boolean;
  onMutedChange?: (muted: boolean) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // A reel starts muted because every current autoplay policy requires it
  // and because a phone that starts making noise in a scroll feed is
  // hostile. Sound is opt-in, one tap away.
  const [localMuted, setLocalMuted] = useState(true);
  const isControlled = mutedProp !== undefined;
  const muted = isControlled ? mutedProp : localMuted;
  const setMuted = (next: boolean) => {
    if (!isControlled) setLocalMuted(next);
    onMutedChange?.(next);
  };
  const [failed, setFailed] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);

  const [liked, setLiked] = useState(false);
  const [likeCount, setLikeCount] = useState(reel.like_count);
  const [announcement, setAnnouncement] = useState("");

  // Respect the OS "reduce motion" setting. Motion sickness is not the
  // only reason someone sets it, but auto-starting video nobody asked for
  // is exactly the kind of uninvited movement it exists to suppress.
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduceMotion(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  const play = useCallback((): void => {
    const video = videoRef.current;
    if (!video) return;
    const attempt = video.play();
    // play() rejects when the browser decides we have not had a user
    // gesture yet (Low Power Mode, Data Saver, some embedded webviews).
    // Swallowing it here means the tap-to-play overlay can take over
    // instead of the console filling with unhandled rejections.
    if (attempt) attempt.then(() => setBlocked(false)).catch(() => setBlocked(true));
  }, []);

  // Play only the reel the feed says is on screen, and never when the
  // reader has asked for less motion. Everything else stays paused: six
  // clips decoding at once is what makes a feed hot on a phone.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || failed) return;
    if (!active || reduceMotion) {
      video.pause();
      return;
    }
    play();
  }, [active, reduceMotion, failed, play]);

  useEffect(() => {
    const onChange = () =>
      setIsFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleSound = () => {
    const video = videoRef.current;
    const next = !muted;
    setMuted(next);
    if (video) {
      video.muted = next;
      // Unmuting a clip that autoplay blocked is a user gesture, which is
      // exactly the permission play() was missing a moment ago.
      if (next && video.paused) play();
    }
  };

  /*
    Keep the element in step with the shared state.

    `muted` on <video> is a DOM property React does not reliably re-apply
    on re-render, which is why toggleSound sets it imperatively. That left
    one gap: a reel that MOUNTS after the reader unmuted would only see
    the prop, and any later change to the shared state would not reach an
    already-mounted element. Syncing it here covers both — and it is what
    makes sound persist across the whole feed rather than per clip.
  */
  useEffect(() => {
    const video = videoRef.current;
    if (video && video.muted !== muted) video.muted = muted;
  }, [muted]);

  const toggleFullscreen = () => {
    const container = containerRef.current;
    if (!container) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen?.().catch(() => undefined);
    } else {
      void container.requestFullscreen?.().catch(() => undefined);
    }
  };

  /**
   * Optimistic like: the heart fills the instant it is tapped, because a
   * like that waits on a round trip feels broken. A failure rolls the
   * number back and says so out loud rather than leaving a lie on screen.
   */
  const toggleLike = async () => {
    const next = !liked;
    setLiked(next);
    setLikeCount((n) => n + (next ? 1 : -1));
    setAnnouncement(next ? "Liked." : "Like removed.");

    try {
      const response = await fetch("/api/reels/like", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reelId: reel.id, liked: next }),
      });
      if (!response.ok) throw new Error(`like failed: ${response.status}`);
    } catch {
      setLiked(!next);
      setLikeCount((n) => n + (next ? -1 : 1));
      setAnnouncement("That like did not save. Try again.");
    }
  };

  const share = async () => {
    const url = canonicalUrl();
    const title = reel.title ?? reel.description ?? "Watch this reel";

    // navigator.share puts the reel in front of a person who can see it;
    // the clipboard is the fallback for a desktop browser that has none.
    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({ title, url });
        return;
      } catch {
        // A cancelled share sheet is not an error to report — the reader
        // chose to back out. Fall through to the clipboard so the tap
        // still did something.
      }
    }

    // `navigator.clipboard` is absent outside a secure context, and a
    // rejected promise covers the case where the reader denied permission.
    // Both are handled: announcing "Link copied" without a copy behind it
    // is the one outcome worse than saying nothing happened.
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(url);
      setAnnouncement("Link copied");
    } catch {
      setAnnouncement("Copying is blocked in this browser.");
    }
  };

  const duration = formatDuration(reel.duration_seconds);
  const caption = reel.title ?? reel.description;

  // Overlay button styling, repeated so each control is self-describing.
  const control =
    "pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full bg-black/50 text-white backdrop-blur transition hover:bg-black/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-black/40";

  return (
    <div
      ref={containerRef}
      className="group relative aspect-[9/16] w-full overflow-hidden rounded-lg border border-gray-200 bg-gray-900 shadow-sm dark:border-gray-700"
    >
      {/*
        The poster sits UNDERNEATH the video rather than only in its
        poster attribute. SafeImage falls back on error, so a dead poster
        CDN leaves a real picture instead of the black rectangle a bare
        <video> shows when nothing has decoded yet.
      */}
      <SafeImage
        src={reel.poster_url ?? ""}
        alt={reel.title ?? "Reel poster"}
        className="absolute inset-0 h-full w-full object-cover"
      />

      {!failed && (
        <video
          ref={videoRef}
          src={reel.video_url}
          poster={reel.poster_url ?? undefined}
          className="absolute inset-0 z-10 h-full w-full object-cover"
          // muted here AND on the element property: React's muted prop is
          // the one browsers check when deciding to autoplay.
          muted={muted}
          loop
          playsInline
          preload="metadata"
          onError={() => setFailed(true)}
        />
      )}

      {/*
        A clip that 404s used to leave a black hole in the grid. Say what
        happened over the poster that did load, so the failure is honest
        and the reader keeps the caption and the credit.
      */}
      {failed && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-1 bg-black/55 px-6 text-center">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 9v4m0 4h.01M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <p className="text-sm font-semibold text-white">Replay unavailable</p>
          <p className="text-xs text-white/70">This clip could not be loaded.</p>
        </div>
      )}

      {/* The browser refused to autoplay; offer the one tap it wanted. */}
      {!failed && blocked && (
        <button
          type="button"
          onClick={play}
          className="absolute inset-0 z-20 flex items-center justify-center bg-black/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white"
        >
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5.5v13l11-6.5-11-6.5Z" />
            </svg>
            <span className="sr-only">Play reel</span>
          </span>
        </button>
      )}

      {/* ================= TOP: sound, length, fullscreen ================= */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-start justify-between gap-2 p-2">
        {duration && (
          <span className="pointer-events-none rounded bg-black/60 px-2 py-1 text-[11px] font-medium tabular-nums text-white backdrop-blur">
            {duration}
          </span>
        )}

        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={toggleSound}
            aria-pressed={!muted}
            aria-label={muted ? "Unmute reel" : "Mute reel"}
            title={muted ? "Unmute reel" : "Mute reel"}
            className={control}
          >
            {muted ? (
              <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M11 5 6.5 9H3v6h3.5L11 19V5Z" fill="currentColor" />
                <path
                  d="M15.5 9.5 21 15m0-5.5L15.5 15"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                />
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M11 5 6.5 9H3v6h3.5L11 19V5Z" fill="currentColor" />
                <path
                  d="M14.6 8.8a4.4 4.4 0 0 1 0 6.4M17.6 5.9a8.8 8.8 0 0 1 0 12.2"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
            )}
          </button>

          {/* Mobile only. In a phone feed this clip is the whole screen
              and there is no browser UI left to tap; on a desktop grid the
              card is one tile among many and going fullscreen steals the
              rest of the page. */}
          <button
            type="button"
            onClick={toggleFullscreen}
            aria-pressed={isFullscreen}
            aria-label={isFullscreen ? "Exit fullscreen" : "Play fullscreen"}
            title={isFullscreen ? "Exit fullscreen" : "Play fullscreen"}
            className={`${control} sm:hidden`}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M9 4H4v5M15 4h5v5M9 20H4v-5m11 5h5v-5"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </div>

      {/* ================= BOTTOM =================
          Everything here sits over a scrim so white text stays legible
          whatever the footage does underneath it. The overlay is
          pointer-events-none; only the actual controls take taps, so the
          rest of the surface still reaches the video. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 flex flex-col gap-2 bg-gradient-to-t from-black/90 via-black/60 to-transparent p-3">
        <div className="pointer-events-auto flex items-center gap-2">
          <button
            type="button"
            onClick={toggleLike}
            aria-pressed={liked}
            aria-label={liked ? "Unlike this reel" : "Like this reel"}
            className={`${control} ${liked ? "text-rose-500" : ""}`}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                fill={liked ? "currentColor" : "none"}
              />
            </svg>
            <span className="ml-1 text-xs font-semibold tabular-nums text-white">
              {formatCount(likeCount)}
            </span>
          </button>

          <button
            type="button"
            onClick={share}
            aria-label="Share this reel"
            title="Share this reel"
            className={control}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 15V3m0 0L8 7m4-4 4 4M5 12v7a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-7"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        {/* Caption above the credit: the reel is the reason to stop
            scrolling, the licence is the reason we are allowed to show
            it, and the eye should meet them in that order. */}
        {caption && (
          <p className="line-clamp-2 text-sm font-semibold leading-snug text-white">
            {caption}
          </p>
        )}

        {/*
          ================= THE CREDIT =================
          Always rendered, collapsed by default, one 44px tap from the
          bottom edge of the frame. Not conditional on
          `attribution_required` and not gated on hover — that flag is on
          the Reel type and is deliberately not read here, because a
          licence flag that can switch the credit off is a licence flag
          with a default that erases someone's name.
        */}
        <details className="group/details pointer-events-auto">
          <summary
            className="flex min-h-[44px] cursor-pointer list-none items-center justify-between gap-2 rounded px-2 text-xs font-medium uppercase tracking-wider text-white/85 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white [&::-webkit-details-marker]:hidden"
          >
            <span>Details</span>
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              className="shrink-0 transition-transform duration-200 group-open/details:rotate-180"
            >
              <path
                d="m6 9 6 6 6-6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </summary>

          <div className="space-y-1.5 pb-1 pt-1 text-xs leading-relaxed text-white/90">
            {reel.creator_name ? (
              <p>
                <span className="text-white/60">By </span>
                {reel.creator_url ? (
                  <a
                    href={reel.creator_url}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                  >
                    {reel.creator_name}
                  </a>
                ) : (
                  <span>{reel.creator_name}</span>
                )}
              </p>
            ) : (
              // No named creator is not a reason to lose the rest of the
              // credit: the licence and the source still apply.
              <p className="text-white/60">Creator not credited</p>
            )}

            {reel.license_name && (
              <p>
                <span className="text-white/60">Licence </span>
                {reel.license_url ? (
                  <a
                    href={reel.license_url}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                  >
                    {reel.license_name}
                  </a>
                ) : (
                  <span>{reel.license_name}</span>
                )}
              </p>
            )}

            <a
              href={`${SITE_URL}/category/reels`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              Watch on source
            </a>
          </div>
        </details>
      </div>

      {/* The like, the share result and any failure are announced without
          costing a visible toast on top of the footage. */}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}