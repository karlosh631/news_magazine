"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SafeImage } from "@/components/SafeImage";
import { playsHere } from "@/lib/reel-codec";
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

/**
 * The library a clip came from, in the reader's language.
 *
 * Pixabay's API terms require it: "If you make use of the API, show your
 * users where the images and videos are from, whenever search results are
 * displayed." That is a separate duty from the per-clip credit, and the
 * credit does not cover it — the uploader named in `<details>` is a
 * Pixabay *user*, not Pixabay.
 *
 * An unrecognised key is title-cased rather than hidden. Hiding it would
 * satisfy the letter of the rule on the day a new provider ships and
 * break it on the day it is used, which is the failure mode worth
 * designing against.
 */
function providerLabel(sourceKey: string | null): string | null {
  if (!sourceKey) return null;
  const known: Record<string, string> = {
    pixabay: "Pixabay",
    pexels: "Pexels",
    wikimedia: "Wikimedia Commons",
    youtube: "YouTube",
  };
  const label = known[sourceKey];
  if (label) return label;
  return sourceKey.charAt(0).toUpperCase() + sourceKey.slice(1);
}

/**
 * True when this clip has to be rendered as an <iframe>.
 *
 * Branching on the FIELD rather than on `source_key` is the point: a new
 * provider that cannot offer a directly playable file sets `embed_url`
 * and gets the right player, without this component learning its name.
 */
function isEmbedded(reel: Reel): boolean {
  return typeof reel.embed_url === "string" && reel.embed_url.length > 0;
}

/**
 * The iframe src for an embedded clip.
 *
 * `enablejsapi=1` is what makes the frame drivable by postMessage at
 * all, and it is required for the play/pause/mute effects below — without
 * it the embed is a static box that only the reader's own clicks can
 * drive, and the feed's one-clip-at-a-time rule silently stops working.
 *
 * `rel=0` suppresses recommendations from other channels, which is both
 * tasteful and the difference between a reader leaving on our URL and
 * being handed to a competitor by the last clip they watched.
 *
 * `mute=1` is fixed in the src and never changes. It is here because
 * every current autoplay policy refuses an unmuted one, and the feed
 * starts muted by design. It is NOT tied to the `muted` state: a src
 * that changed with the mute button would re-navigate the iframe on
 * every unmute and restart the clip from zero, so unmuting is done by
 * postMessage instead. See the mute effect below.
 */
function embedPlayerSrc(embedUrl: string | null): string | null {
  if (!embedUrl) return null;
  try {
    const url = new URL(embedUrl);
    url.searchParams.set("enablejsapi", "1");
    url.searchParams.set("rel", "0");
    url.searchParams.set("playsinline", "1");
    url.searchParams.set("modestbranding", "1");
    url.searchParams.set("mute", "1");
    return url.toString();
  } catch {
    // A malformed embed URL is a provider bug, not a reason to render a
    // frame pointing at nothing. The caller falls back to the poster.
    return null;
  }
}

/**
 * The exact origin an embedded frame lives on.
 *
 * Used to target postMessage and to reject inbound ones. `"*"` is the
 * wrong value for both: as a send target it publishes the commands to
 * whatever document is in the frame, and as a filter it accepts them
 * from anywhere on the internet.
 */
function embedOrigin(embedUrl: string | null): string | null {
  if (!embedUrl) return null;
  try {
    return new URL(embedUrl).origin;
  } catch {
    return null;
  }
}

export function ReelCard({
  reel,
  active = false,
  mountEmbed = true,
  muted: mutedProp,
  onMutedChange,
}: {
  reel: Reel;
  active?: boolean;
  /**
   * Whether this card may instantiate an embedded player at all.
   *
   * The feed renders BOTH its mobile and desktop layouts at once and
   * hides one with CSS, so every reel exists twice in the DOM. That is
   * free for a <video> — a hidden copy costs nothing to decode — but an
   * <iframe> is not free at all: each one is a whole YouTube IFrame
   * Player, fetching YouTube's player script and establishing its own
   * session. Duplicating the feed that way is two players per clip and
   * twenty-four on a twelve-card page, which is enough to stall the page
   * and to get this host throttled by Google.
   *
   * So the feed mounts the embed for the one clip that is both on screen
   * and active, and every other card shows its poster — which is what a
   * <video> with preload="metadata" costs anyway.
   *
   * Defaults to true so the component still works on its own.
   */
  mountEmbed?: boolean;
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
  const frameRef = useRef<HTMLIFrameElement>(null);

  /**
   * Whether this card has to drive an embedded player instead of a
   * <video>. See `isEmbedded` and `embedPlayerSrc`.
   */
  const embedded = isEmbedded(reel);
  const frameSrc = useMemo(() => embedPlayerSrc(reel.embed_url), [reel.embed_url]);
  /** An embed is only mounted when the feed says this clip is the one. */
  const embedMounted = embedded && mountEmbed && frameSrc !== null;
  const frameOrigin = useMemo(() => embedOrigin(reel.embed_url), [reel.embed_url]);

  /**
   * Whether an embedded player has announced itself.
   *
   * postMessage to a frame that has not finished loading is simply
   * dropped on the floor, so the very first playVideo() would be lost
   * and the card would sit still while every other clip in the feed
   * moved. The player announces readiness itself, so we wait for that
   * rather than guessing with a timeout.
   */
  const [frameReady, setFrameReady] = useState(false);

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
  /**
   * Whether THIS browser can decode THIS clip's container.
   *
   * Starts true and is corrected after mount, so the server render and
   * the first client render agree and there is no hydration mismatch —
   * and so the default, if the check never runs or throws, is to play
   * the clip rather than to hide it.
   *
   * This is the only place a reel is withheld from a reader. It fires
   * for exactly one case: a webm on a browser that says it cannot decode
   * webm, which today means Safari on iOS. See lib/reel-codec.ts for why
   * the question is asked of the browser rather than guessed from the
   * user agent, and why nothing else is filtered here — formats that no
   * browser plays are removed on the server instead.
   */
  const [playableHere, setPlayableHere] = useState(true);
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

  // Ask the browser what it can decode, once mounted.
  //
  // Deliberately after mount rather than during render: `canPlayType`
  // needs a `document`, and running it in the server render would either
  // throw or — via the SSR guard in playsHere — bake "assume it plays"
  // into the HTML and then correct it, which is a hydration mismatch.
  useEffect(() => {
    // Read through the two fields rather than the row, so the dependency
    // list is exactly the data this decision is made from.
    const embed = reel.embed_url;
    const embedded = typeof embed === "string" && embed.length > 0;
    setPlayableHere(playsHere(reel.video_url, embedded));
  }, [reel.video_url, reel.embed_url]);

  /**
   * Send a command to the embedded player.
   *
   * Only meaningful when `embedded` is true; on a plain <video> card it
   * finds no frame and returns. Declared above `play` because `play`
   * closes over it.
   */
  const command = useCallback(
    (func: string): void => {
      const frame = frameRef.current;
      if (!frame || !frameOrigin) return;
      frame.contentWindow?.postMessage(
        JSON.stringify({ event: "command", func, args: [] }),
        frameOrigin,
      );
    },
    [frameOrigin],
  );

  const play = useCallback((): void => {
    if (embedded) {
      // An embedded player has no play() promise to await, so there is
      // nothing to set `blocked` from. It starts muted (see
      // embedPlayerSrc), which is what every autoplay policy requires,
      // so the tap-to-play fallback is not reachable here in the first
      // place — claiming otherwise would be inventing a state we cannot
      // observe.
      command("playVideo");
      return;
    }

    const video = videoRef.current;
    if (!video) return;
    const attempt = video.play();
    // play() rejects when the browser decides we have not had a user
    // gesture yet (Low Power Mode, Data Saver, some embedded webviews).
    // Swallowing it here means the tap-to-play overlay can take over
    // instead of the console filling with unhandled rejections.
    if (attempt) attempt.then(() => setBlocked(false)).catch(() => setBlocked(true));
  }, [embedded, command]);

  // Play only the reel the feed says is on screen, and never when the
  // reader has asked for less motion. Everything else stays paused: six
  // clips decoding at once is what makes a feed hot on a phone.
  useEffect(() => {
    if (embedded) return; // handled by the embed effect below
    const video = videoRef.current;
    if (!video || failed) return;
    if (!active || reduceMotion) {
      video.pause();
      return;
    }
    play();
  }, [active, reduceMotion, failed, play, embedded]);

  // Wait for the player to announce itself before driving it.
  //
  // Keyed on `embedMounted` rather than `embedded`, and that is the point
  // rather than a detail. When the feed unmounts this card's iframe the
  // cleanup clears `frameReady`, so the next mount — a fresh <iframe>,
  // a fresh player that has not finished loading — starts from false
  // and waits for its own onReady. Leaving it true would send the first
  // playVideo into a frame that is not listening yet, lose it, and
  // leave the card permanently still.
  useEffect(() => {
    if (!embedMounted) return;
    if (!frameOrigin) return;

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== frameOrigin) return;
      if (typeof event.data !== "string") return;
      try {
        if (JSON.parse(event.data)?.event === "onReady") setFrameReady(true);
      } catch {
        // Not one of ours. The player also posts infoDelivery objects,
        // and anything at all can post anything at all.
      }
    };

    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      setFrameReady(false);
    };
  }, [embedMounted, frameOrigin]);

  // The embedded equivalent of the play/pause effect above. Gated on
  // readiness rather than fired blind, because a command sent to a
  // loading frame is discarded.
  useEffect(() => {
    if (!embedded || !frameReady) return;
    command(active && !reduceMotion && !failed ? "playVideo" : "pauseVideo");
  }, [embedded, frameReady, active, reduceMotion, failed, command]);

  useEffect(() => {
    const onChange = () =>
      setIsFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleSound = () => {
    const next = !muted;
    setMuted(next);

    if (embedded) {
      // Unmuting is a user gesture, which is exactly the permission an
      // autoplay-blocked player was missing a moment ago — so send it
      // and start the clip while we have it. Deliberately NOT written
      // into the iframe src: changing that would re-navigate the frame
      // and restart the clip from zero.
      command(next ? "unMute" : "mute");
      if (next && !frameReady) command("playVideo");
      return;
    }

    const video = videoRef.current;
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

    An embedded player has the same gap for the same reason, and needs
    it just as much: the shared sound state survives swiping past the
    clip that was unmuted, and a reel that MOUNTS mid-session unmuted
    must not come up silent. Gated on readiness, because a mute command
    sent to a loading frame is discarded — which would silently leave the
    clip muted with no way to recover but the button.
  */
  useEffect(() => {
    if (embedded) {
      if (frameReady) command(muted ? "mute" : "unMute");
      return;
    }
    const video = videoRef.current;
    if (video && video.muted !== muted) video.muted = muted;
  }, [muted, embedded, frameReady, command]);

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
  const provider = providerLabel(reel.source_key);

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

      {/*
        Two players, one card.

        A provider that offers a directly playable file gets a <video>,
        which is every source except YouTube and has been since 0014.
        A provider that set `embed_url` gets an <iframe> instead, because
        its `video_url` is an HTML document — pointing a <video> at one
        produces an instant MEDIA_ERR_SRC_NOT_SUPPORTED and a card that
        says "Replay unavailable" for the whole feed.

        The two are mutually exclusive on purpose. A <video> pointed at
        an embed URL and an <iframe> pointed at an mp4 are both broken,
        so exactly one renders.
      */}
      {!failed && embedMounted && frameSrc && (
        <iframe
          ref={frameRef}
          src={frameSrc}
          title={reel.title ?? "Reel"}
          className="absolute inset-0 z-10 h-full w-full border-0"
          /*
            `autoplay` is required in the allow list or the player is
            refused outright — every autoplay policy in current browsers
            consults it, and YouTube's player checks it before its own
            autoplay logic runs. `encrypted-media` and `picture-in-picture`
            are what the YouTube player asks for and refuses to start
            without in some browsers.
          */
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
          /*
            Sends the origin rather than the full URL, so nothing about
            this page's own referrer reaches YouTube. `strict-origin-when-
            cross-origin` is the browser default written out, because the
            reason to set it here is to not have to think about it again.
          */
          referrerPolicy="strict-origin-when-cross-origin"
        />
      )}

      {/*
        A <video> ONLY for a clip that is genuinely a media file — hence
        the `!embedded` guard, not merely `!embedMounted`.

        An embedded clip that is not currently mounted (the copy in the
        hidden layout, or any card the reader has not reached) must NOT
        render a <video>: its `video_url` is an HTML document, so the
        element would fail immediately and paint "Replay unavailable"
        over every such card. Those cards fall through to nothing at all,
        and the poster underneath carries them — which is exactly what a
        <video> with preload="metadata" costs for a plain one anyway.

        `playableHere` is the last guard and the narrowest. Mounting a
        <video> at a container this browser has told us it cannot decode
        costs a full fetch of the file, a decode attempt, an error event
        and a black frame — on every card in the feed, twice over,
        because the mobile and desktop layouts both exist in the DOM.
        Skipping it leaves the poster, which has already loaded, in
        place of a rectangle that was never going to show anything.
      */}
      {!failed && !embedMounted && !embedded && playableHere && (
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

      {/*
        The clip is fine; this browser is not the audience for it.

        Deliberately NOT the "Replay unavailable" panel above, which says
        the file could not be loaded — a statement that would be false
        here. The file loaded. It is a WebM, and this engine cannot
        decode WebM, which today means Safari on iOS. The difference
        matters to the reader: one is a broken site and the other is a
        clip that was never going to play on this device.

        The poster stays underneath, the caption and credit stay
        readable, and the link out goes to the source — which plays it,
        because Commons's own page uses the same webm and has the same
        problem, so the honest destination is the file's description
        page rather than a promise about playback.
      */}
      {!failed && !playableHere && (
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
          <p className="text-sm font-semibold text-white">Not playable on this browser</p>
          <p className="text-xs text-white/70">
            This clip is published as a WebM video, which Safari on iPhone and iPad cannot
            decode.
          </p>
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
        <div className="flex flex-wrap items-center gap-1.5">
          {duration && (
            <span className="pointer-events-none rounded bg-black/60 px-2 py-1 text-[11px] font-medium tabular-nums text-white backdrop-blur">
              {duration}
            </span>
          )}
          {/*
            The library, always visible. Not in the credit block and not
            behind a tap: Pixabay's API terms ask for the origin of every
            result to be shown to the user, which a disclosure nobody opens
            does not do.
          */}
          {provider && (
            <span className="pointer-events-none rounded bg-black/60 px-2 py-1 text-[11px] font-medium text-white/90 backdrop-blur">
              {provider}
            </span>
          )}
        </div>

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