/**
 * What this browser can actually decode.
 *
 * This file is the CLIENT-SAFE half of the reels rules: the parts the
 * browser and the server both have to agree on, kept out of
 * lib/reels.ts because that one is `server-only` — it holds the
 * database client, and a client component importing it does not build.
 *
 * WHY THIS EXISTS, IN ONE PARAGRAPH
 *
 * Wikimedia Commons publishes no H.264 mp4. Checked against the live API
 * across a 50-file sample (184 derivative entries) and against the CDN:
 * every transcoded rendition it offers is `.vp9.webm`, `.mpeg4.mov`
 * (MPEG-4 Part 2) or `.theora.ogv`, and every `.480p.mp4` path answers
 * HTTP 404. There is no ingestion change that turns those into something
 * Safari on iOS will play, because the bytes do not exist upstream and
 * this project stores URLs and metadata, never video. A reel card pointed
 * at a webm on an iPhone is therefore going to fail, and the only
 * remaining question is what to put in the DOM when we know it will.
 *
 * WHY A CAPABILITY PROBE AND NOT A USER-AGENT
 *
 * Sniffing `User-Agent` to decide "is this Safari" is how a read-path
 * filter ends up hiding good content from everybody: proxies rewrite
 * user agents, bots and crawlers send none at all, and a rule that is
 * wrong in the hiding direction silently empties the feed for a whole
 * class of reader with nothing in the logs to say why.
 *
 * So nothing here reads the user agent. `canPlayType()` asks the browser
 * the only question that matters — can YOU decode this? — and returns
 * "" (empty string) when the answer is no. It is a fact about the
 * rendering engine rather than a guess about a string, it is exactly as
 * current as the browser asking, and it needs no server-side
 * negotiation.
 *
 * WHY IT DEFAULTS TO SHOWING
 *
 * Three ways to get this wrong, all of which lose content rather than
 * merely failing to hide it: a probe that throws, a probe run during
 * server rendering where `document` does not exist, and a browser that
 * answers "maybe" rather than a yes or a no. Every one of them shows
 * the clip. The failure mode of this module is a dead card on one
 * device, never an empty page.
 */

/**
 * The codec string the probe asks about.
 *
 * VP9 is the codec every webm Commons actually serves, and it is the
 * one Safari on iOS cannot decode. VP8 is a subset of what any VP9
 * decoder handles, so probing VP9 covers both.
 */
const WEBM_PROBE = 'video/webm; codecs="vp9"';

/**
 * Cached once per page load.
 *
 * The answer cannot change while the document lives, and ReelCard
 * renders every reel twice (mobile and desktop layouts exist in the DOM
 * together), so without this a twelve-card feed would run twenty-four
 * throwaway `<video>` elements to learn the same thing repeatedly.
 */
let webmDecodable: boolean | null = null;

/**
 * True when the browser reports it cannot decode WebM.
 *
 * `canPlayType` answers "probably", "maybe" or "". Only "" is a no. A
 * "maybe" is uncertainty about performance rather than about support,
 * and treating it as a no would hide clips that do play.
 */
export function canDecodeWebm(): boolean {
  if (webmDecodable !== null) return webmDecodable;
  if (typeof document === "undefined") {
    // Server render. There is no browser to ask, and the right answer is
    // the one that shows the clip: the client re-evaluates on mount.
    return true;
  }
  try {
    webmDecodable = document.createElement("video").canPlayType(WEBM_PROBE) !== "";
  } catch {
    // A browser that refuses to create a probe element is a browser we
    // know nothing about. Show the clip.
    webmDecodable = true;
  }
  return webmDecodable;
}

/**
 * True when this URL is a webm, and so depends on WebM support to play.
 *
 * Narrow by construction: it looks at the extension and nothing else. It
 * does not guess at codecs from a query string, does not treat an
 * unfamiliar URL as webm, and does not look at a `<video>`'s `type`
 * attribute — the row has one URL and the player has one question to
 * ask about it.
 *
 * The `?` is stripped because Wikimedia appends analytics parameters to
 * the URL it returns, which is what makes a naive `endsWith(".webm")`
 * fail on exactly the rows this is here to catch.
 */
export function isWebmUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  const path = url.split("?")[0]!.split("#")[0]!.toLowerCase();
  return path.endsWith(".webm");
}

/**
 * Whether this reel cannot play in THIS browser, as opposed to in some
 * browser somewhere.
 *
 * Only webm is considered. The formats no browser plays at all — Ogg
 * Video, and a YouTube embed URL with no companion `embed_url` — are
 * filtered on the server in `isPlayableReel`, because they are wrong
 * everywhere and hiding them costs nothing. WebM is different: it is
 * right for most of the audience and wrong only for a specific family
 * of engines, so the decision belongs with the engine.
 */
export function playsHere(videoUrl: string | null | undefined, embedded: boolean): boolean {
  if (embedded) return true; // an <iframe> player brings its own codecs
  if (!isWebmUrl(videoUrl)) return true;
  return canDecodeWebm();
}

/**
 * The provider key for reels built from this site's own news.
 *
 * A row with this source_key is NOT a video: it is a story's own
 * verified photograph and its headline, stored as a reel so the Reels
 * page moves in step with the news page. There is no file for a
 * browser to decode, so the card must not ask it to.
 *
 * The key is half of the store's dedup pair and is written by the
 * ingestion worker; it is named here so the read path and the card
 * agree on what an image reel is.
 *
 * It lives in this file rather than in lib/reels.ts because that one is
 * `server-only` — it holds the database client — and the card is a
 * client component. A client component that imports a server-only
 * module does not build.
 */
export const ARTICLE_REELS_SOURCE_KEY = "articles";

/**
 * True when this reel is a story rather than a clip.
 *
 * Branches on the provider key rather than on the shape of the URL,
 * because "does this URL end in .jpg" is a guess about a CDN and the
 * provider key is a fact.
 */
export function isArticleReel(reel: {
  source_key: string | null | undefined;
}): boolean {
  return reel.source_key === ARTICLE_REELS_SOURCE_KEY;
}

/**
 * One of this site's own stories, as the reels feed needs it.
 *
 * The article's uuid IS the reel's `external_id`, so this is how a
 * clip is matched to the story it came from.
 */
export interface VideoStory {
  id: string;
  slug: string;
  video_url: string | null;
  video_embed_url: string | null;
}

/**
 * A direct media file, by extension.
 *
 * The same rule the site's own news cards use: an `og:video` can point
 * at a share page or a playlist, and a `<video>` pointed at one never
 * plays.
 */
const DIRECT_VIDEO_RE = /\.(mp4|webm|mov|m4v|ogv|ogg)(\?|$)/i;

/**
 * The clip a story carries, and the player it needs.
 *
 * The STORY is the authority on its own media: the reels row is a
 * cache of it, and a row stored before the story carried a video holds
 * the story's PHOTOGRAPH in `video_url`. A photograph handed to a
 * `<video>` element is a card that says "Replay unavailable", so the
 * story's own video is used.
 *
 * Returns (videoUrl, embedUrl):
 *
 *   - a direct file -> (the file, null): a `<video>` plays it;
 *   - a player URL  -> (the player, the player): an `<iframe>` plays
 *     it, because a player URL is an HTML document and `<video src>`
 *     refuses one;
 *   - neither        -> (null, null): the story has no clip, and the
 *     row is a picture and not a broken video.
 */
export function storyMedia(
  story: VideoStory | undefined | null
): { videoUrl: string | null; embedUrl: string | null } {
  if (!story) return { videoUrl: null, embedUrl: null };

  const file =
    story.video_url && DIRECT_VIDEO_RE.test(story.video_url)
      ? story.video_url
      : null;
  const player = story.video_embed_url ?? null;

  if (file) return { videoUrl: file, embedUrl: player };
  if (player) return { videoUrl: player, embedUrl: player };
  return { videoUrl: null, embedUrl: null };
}

/**
 * The link to the story behind a clip.
 *
 * The operator's complaint was that the story cards "are also not
 * clickable and not redirecting on mysite news". This is the answer:
 * the clip's `external_id` is the article's uuid, the feed resolves it
 * to a slug, and the card links to `/article/<slug>`.
 *
 * It is not a database column: the reels table has no slug and no
 * article reference, and adding one is a migration, which is the
 * operator's job. The link is computed at read time instead.
 */
export function storyLink(
  externalId: string | null | undefined,
  stories: Map<string, VideoStory> | Record<string, VideoStory>
): string | null {
  if (!externalId) return null;
  const story =
    stories instanceof Map ? stories.get(externalId) : stories[externalId];
  if (!story?.slug) return null;
  return `/article/${story.slug}`;
}

/**
 * THE POSTER, AT THE WIDTH THE PROVIDER ACTUALLY HAS
 *
 * Wikimedia serves a video's poster at a fixed ladder of widths and
 * answers HTTP 400 for anything else. Measured against the live CDN,
 * one width at a time:
 *
 *   120, 250, 330, 500, 960, 1280, 1920, 3840 -> a JPEG of that width
 *   everything else                          -> HTTP 400, an HTML body
 *
 * So "1440" — the width the operator asked for — is not a rung. The
 * best rung at or below 1440 is 1280, and the best at or below 1080 is
 * 960. A rung wider than the source is never chosen either: the thumb
 * handler will happily scale a 720-wide frame up to 1280, and a poster
 * that claims detail the footage does not have is a picture of
 * something that is not there.
 *
 * These are the same numbers the ingestion uses (see
 * `commons_poster_width` in app/services/reels/providers.py). They live
 * here as well because this file is safe for the browser and the
 * poster URL is built on the server, and a rule that has to be written
 * down twice should at least be written down in the same words.
 */
export const WIKIMEDIA_THUMBNAIL_WIDTHS: readonly number[] = [
  120, 250, 330, 500, 960, 1280, 1920, 3840,
];

/** The poster width we would like. */
export const WIKIMEDIA_PREFERRED_POSTER_WIDTH = 1440;

/** The poster width we settle for. */
export const WIKIMEDIA_FALLBACK_POSTER_WIDTH = 1080;

/**
 * The widest poster the provider has for a source this wide.
 *
 * `sourceWidth` is the width of the picture the poster is cut from,
 * and `null` means "we do not know" — in which case the conservative
 * answer is the fallback, because a smaller real picture beats a
 * larger invented one.
 */
export function posterWidthFor(sourceWidth: number | null | undefined): number {
  const ladder = WIKIMEDIA_THUMBNAIL_WIDTHS;
  const limit = Number(sourceWidth);
  const known = Number.isFinite(limit) && limit > 0;

  /*
    An unknown width is answered with the fallback alone: we cannot
    tell whether the provider has a wider poster, and a smaller real
    picture beats a larger invented one.
  */
  const ceilings = known
    ? [WIKIMEDIA_PREFERRED_POSTER_WIDTH, WIKIMEDIA_FALLBACK_POSTER_WIDTH]
    : [WIKIMEDIA_FALLBACK_POSTER_WIDTH];

  for (const ceiling of ceilings) {
    const allowed = known ? Math.min(ceiling, limit) : ceiling;
    const rungs = ladder.filter((width) => width <= allowed);
    if (rungs.length > 0) return Math.max(...rungs);
  }

  return ladder[0] as number;
}

/**
 * What the browser knows about a clip's audio.
 *
 * "yes"  — there is an audio track;
 * "no"   — there is provably not one;
 * "unknown" — the browser has not told us yet, or cannot.
 *
 * WHY IT IS ASKED OF THE BROWSER
 * ------------------------------
 * The reels table has no column for it, and it cannot have one: a
 * migration is the operator's job and a silent clip is not worth a
 * schema change. But the browser already knows the answer, and it
 * knows it from the file itself:
 *
 *   - Firefox answers `mozHasAudio` outright;
 *   - Chrome and Safari expose `webkitAudioDecodedByteCount`, which
 *     counts the audio bytes actually decoded — zero for a silent
 *     clip, and it only means anything once the clip has played;
 *   - the standard `audioTracks` list, where an engine has it.
 *
 * So a card that shows a speaker button on a clip with no sound is
 * making a promise the file cannot keep, and this is what stops it.
 */
export type AudioState = "yes" | "no" | "unknown";

export function audioState(video: HTMLVideoElement): AudioState {
  const probe = video as HTMLVideoElement & {
    mozHasAudio?: boolean;
    webkitAudioDecodedByteCount?: number;
    audioTracks?: { length: number };
  };

  // Firefox answers the question directly.
  if (typeof probe.mozHasAudio === "boolean") {
    return probe.mozHasAudio ? "yes" : "no";
  }

  // The standard track list, where an engine has one.
  if (probe.audioTracks && typeof probe.audioTracks.length === "number") {
    return probe.audioTracks.length > 0 ? "yes" : "no";
  }

  // Chrome and Safari count the audio bytes they have decoded.
  if (typeof probe.webkitAudioDecodedByteCount === "number") {
    if (probe.webkitAudioDecodedByteCount > 0) return "yes";
    // Zero only means "silent" once the clip has been playing: before
    // that, nothing has been decoded at all.
    if (video.currentTime > 0) return "no";
    return "unknown";
  }

  return "unknown";
}

/**
 * Newest first, by the moment the worker stored the row.
 *
 * The feed is read in two queries — the clips the providers brought,
 * and the clips built from this site's own stories — so the two halves
 * are merged here rather than in the database.
 *
 * `ingested_at` is the order, and `published_at` breaks the tie:
 * providers publish at whole-second resolution, so two clips routinely
 * share a value, and without a second key the order would shuffle
 * between renders.
 *
 * It is arrival order, not novelty order: an evergreen clip discovered
 * for the first time today rightly appears at the top, because from
 * the reader's point of view it is new. That is what makes the page
 * move on every pass.
 */
export function sortByArrival<
  T extends { ingested_at?: string | null; published_at?: string | null }
>(reels: T[]): T[] {
  return [...reels].sort((a, b) => {
    const left = a.ingested_at ?? "";
    const right = b.ingested_at ?? "";
    if (left !== right) return left < right ? 1 : -1;

    const leftPublished = a.published_at ?? "";
    const rightPublished = b.published_at ?? "";
    if (leftPublished !== rightPublished) {
      return leftPublished < rightPublished ? 1 : -1;
    }
    return 0;
  });
}

