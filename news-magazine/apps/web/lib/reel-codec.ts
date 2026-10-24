/**
 * What this browser can actually decode.
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

