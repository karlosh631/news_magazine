/**
 * Reels data access.
 *
 * Short-form video, filed as the `reels` category. The rows here hold
 * metadata and an upstream URL only — playback streams straight from the
 * provider's CDN, because storing video bytes would bloat the database
 * for no benefit (see migration 0014).
 *
 * It follows the same rules as lib/news.ts, for the same reason: a
 * feature that reads the database must never be the thing that takes the
 * page down. Every fetcher guards on isSupabaseConfigured(), returns a
 * structured { reels, error, configured } instead of throwing, and treats
 * an unapplied migration as an empty feed rather than a 500.
 */

import "server-only";

import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";

import { isSupabaseConfigured } from "@/lib/news";
import { createPublicSupabaseClient } from "@/lib/supabase/server";

/**
 * How many clips one feed request asks for.
 *
 * It was 12, which meant the panel could report 146 clips in the
 * catalogue and the page showed a dozen of them with no way to reach
 * the rest - the reader saw the same twelve for ever and had no
 * reason to believe the catalogue was bigger.
 *
 * The clips are cheap until they are watched: each one is a poster
 * until it is the active reel, and the player is fetched when the
 * reader arrives. So a longer list costs a longer scroll and a few
 * hundred kilobytes of posters, and it means the whole catalogue is
 * reachable.
 */
export const REELS_PAGE_SIZE = 60;

/** Hard ceiling, so a bad caller cannot ask for the whole table. */
const REELS_MAX_LIMIT = 200;

/**
 * Shown whenever the reels tables are not there yet.
 *
 * Migration 0014 is additive and idempotent, so a database that has not
 * had it applied is a perfectly healthy database — it just has no Reels
 * feature yet. That is not an error and must never render as one.
 */
export const REELS_NOT_AVAILABLE_NOTE =
  "Reels are not available yet: apply supabase/migrations/0014_reels.sql to create the reels and reel_likes tables. " +
  "YouTube Shorts additionally need 0015_reels_embed_url.sql; without it those rows are filtered out of the feed rather than shown broken.";

export interface Reel {
  id: string;
  /**
   * Which library this clip came from — `pixabay`, `pexels`, `wikimedia`,
   * `youtube`.
   *
   * This is NOT a display detail. Pixabay's API terms: "If you make use
   * of the API, show your users where the images and videos are from,
   * whenever search results are displayed." The per-clip credit block
   * satisfies the licence; it does not satisfy that, because the uploader
   * of a Pixabay clip is a Pixabay user and not the library itself. The
   * card therefore renders the provider on every reel, unconditionally,
   * not inside the disclosure.
   */
  source_key: string | null;
  video_url: string;
  /**
   * Official provider embed-player src, present only when `video_url`
   * is NOT a directly playable media file (migration 0015).
   *
   * Currently only the YouTube provider sets it. A YouTube embed URL is
   * an HTML document, so `<video src>` refuses it and the card would
   * render "Replay unavailable" — the player branches on this field to
   * choose `<iframe>`, never on `source_key`, so a future provider gets
   * the right player by setting one field. null means a plain media
   * file, which is every other provider and every pre-0015 row.
   */
  embed_url: string | null;
  poster_url: string | null;
  title: string | null;
  description: string | null;
  creator_name: string | null;
  creator_url: string | null;
  license_name: string | null;
  license_url: string | null;
  attribution_required: boolean;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  published_at: string | null;
  like_count: number;
}

export interface ReelFeedResult {
  reels: Reel[];
  error: string | null;
  configured: boolean;
  note?: string;
}

/**
 * The anonymous client, widened to the two tables migration 0014 adds.
 *
 * `createPublicSupabaseClient()` returns a client typed against
 * `@/types/database`, which predates Reels, so `.from("reels")` does not
 * type-check. Casting to the default generic hands back the `any`-typed
 * client every consumer of this library already had.
 *
 * That cast buys convenience, not safety: every column is still named in
 * full in the select below and every field is still mapped one by one, so
 * a renamed column shows up as a null rather than passing silently.
 *
 * It is still the ANON key. Row Level Security remains the authorization
 * boundary, and 0014 opens public select on `reels` plus visitor-scoped
 * insert/delete on `reel_likes` — exactly what this feature needs.
 */
export function createReelsClient(): SupabaseClient {
  return createPublicSupabaseClient({
    revalidate: REELS_READ_REVALIDATE_SECONDS,
  }) as unknown as SupabaseClient;
}

/**
 * How long a reels read may be reused before the database is asked again.
 *
 * Shorter than the site's news window on purpose. The reels query is
 * the heaviest one on the site — a couple of hundred rows, each with an
 * aggregate count of its likes — and it is asked for on every render of
 * a page that is really a video player. Caching it for a minute would
 * make the server much cheaper, but a like the reader has just tapped
 * would not move the number under their thumb for a minute, and that is
 * a number they are looking at.
 *
 * Fifteen seconds is short enough that the count has caught up before
 * anyone looks for it, and long enough that the page is not asking
 * Supabase for the whole catalogue on every scroll.
 */
export const REELS_READ_REVALIDATE_SECONDS = 15;

/**
 * The origin every Wikimedia Commons file is served from.
 */
const WIKIMEDIA_ORIGINALS = "https://upload.wikimedia.org/wikipedia/commons/";

/**
 * The thumbnail width the poster is asked for.
 *
 * Wikimedia only serves a fixed set of thumbnail widths — 120, 250, 330,
 * 500, 960, 1280, 1920, 3840 — and answers anything else with HTTP 400
 * and an HTML error page. A reel card is about 420 pixels wide, so 500
 * is the smallest width that is not visibly soft.
 */
const WIKIMEDIA_POSTER_WIDTH = 500;

/**
 * The poster frame for a Wikimedia clip, rebuilt from the clip's own URL.
 *
 * THE POSTER URL STORED IN THE DATABASE DOES NOT WORK
 * ---------------------------------------------------
 * Every Wikimedia row in `reels` carries a `poster_url` of the shape
 *
 *   /wikipedia/commons/c/ce/thumb/commons/c/ce/720px--NAME.ogv.jpg
 *
 * which is wrong twice over. The path repeats the hash directory
 * (`c/ce` twice), and the width — 720 — is not one of the widths
 * Wikimedia serves. The result is HTTP 400 with an HTML error body, and
 * because the browser asked for an image and got text, Chrome blocks
 * the response outright (net::ERR_BLOCKED_BY_ORB) rather than showing a
 * broken-image icon.
 *
 * Measured on the live reels page: 400 distinct broken poster URLs, 579
 * blocked responses, and the whole page was failing its own network
 * audit because of them.
 *
 * The original file is not hard to find, because the clip's own URL
 * says where it is:
 *
 *   /wikipedia/commons/transcoded/c/ce/NAME.ogv/NAME.ogv.480p.vp9.webm
 *
 * The transcoded derivative is filed under the same hash directory as
 * the original, and MediaWiki's thumbnail of a video is
 *
 *   /wikipedia/commons/thumb/<hash>/<FILE>/<width>px-<FILE>.jpg
 *
 * so the poster follows from the clip's URL with no guesswork. This
 * rebuilds it. The stored URL is used only when the clip is not a
 * Wikimedia one, or when its URL is a shape this does not recognise.
 */
function wikimediaPosterUrl(
  videoUrl: string,
  posterUrl: string | null
): string | null {
  if (!videoUrl.startsWith(WIKIMEDIA_ORIGINALS)) return posterUrl;

  const rest = videoUrl.slice(WIKIMEDIA_ORIGINALS.length);
  const withoutTranscoded = rest.startsWith("transcoded/")
    ? rest.slice("transcoded/".length)
    : rest;

  // "<hash>/<sub>/<FILE>" in both shapes. The transcoded form adds a
  // fourth segment holding the derivative's own name, which is not
  // where the original lives.
  const parts = withoutTranscoded.split("/");
  if (parts.length < 3) return posterUrl;

  const file = parts[2];
  if (!file || !file.includes(".")) return posterUrl;

  const directory = `${parts[0]}/${parts[1]}`;
  return `${WIKIMEDIA_ORIGINALS}thumb/${directory}/${file}/${WIKIMEDIA_POSTER_WIDTH}px-${file}.jpg`;
}

/**
 * True when the failure means "this table does not exist".
 *
 * PostgREST answers a missing relation with PGRST200 from its schema
 * cache, or 42P01 (undefined_table) once it has actually asked Postgres.
 * A partially applied migration also lands here, which is the correct
 * outcome: an empty feed with a note, never a crashed page.
 */
export function isMissingReelsTable(error: PostgrestError): boolean {
  if (error.code === "PGRST200" || error.code === "42P01") return true;
  return /does not exist/i.test(error.message ?? "");
}

/**
 * The Reels feed, newest first.
 *
 * `reel_likes(count)` is a PostgREST aggregate embed: the count comes back
 * with the reels in the same round trip rather than as one query per
 * clip. It rides on the public select policy 0014 grants on both tables,
 * so no service-role key is involved.
 *
 * The read IS cached, for fifteen seconds, and the window is
 * `REELS_READ_REVALIDATE_SECONDS` above. It used to be cached for ever
 * — Next 14 caches a plain GET with no `cache` option indefinitely,
 * which is a different bug and was the reason this file once froze a
 * feed at whatever the database held the first time it was read. A
 * bounded window is the difference between "slightly stale" and
 * "frozen", and a like count fifteen seconds behind is not a lie the
 * reader can see.
 */
export async function getReels(opts?: { limit?: number }): Promise<ReelFeedResult> {
  const limit = clampLimit(opts?.limit);

  if (!isSupabaseConfigured()) {
    return {
      reels: [],
      error:
        "Supabase is not configured. Add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to apps/web/.env.local.",
      configured: false,
    };
  }

  const supabase = createReelsClient();

  const { data, error } = await supabase
    .from("reels")
    .select(
      "id, source_key, video_url, embed_url, poster_url, title, description, creator_name, creator_url, license_name, license_url, attribution_required, width, height, duration_seconds, published_at, reel_likes(count)"
    )
    /*
      Order by WHEN WE FETCHED IT, not by when it was published.

      This was `published_at DESC, ingested_at DESC`, and on a feed of
      Wikimedia clips it produced a page that never changed. A Commons
      clip's `published_at` is its upload date, which for this subject
      means 2011 and 2014 Everest footage. Those dates are frozen for
      ever, so the top of the feed was permanently occupied by whichever
      clips happened to have the newest upload — and every clip the
      worker has ingested since sorts BELOW them. A reader could watch
      the worker add rows all day and the first twelve cards would not
      move.

      `ingested_at` is monotonic and is what the reader is actually
      being promised: the newest thing we have on top. It is the same
      rule the articles feed uses, and for the same reason.

      Note this is arrival order, not novelty order — an evergreen clip
      discovered for the first time today rightly appears at the top,
      because from the reader's point of view it is new. That is what
      makes the page move on every pass.

      `published_at` remains as the tiebreak: providers publish at
      whole-second resolution, so two clips routinely share a value, and
      without a second key the order would shuffle between renders.
    */
    .order("ingested_at", { ascending: false, nullsFirst: false })
    .order("published_at", { ascending: false, nullsFirst: false })
    // Over-fetched so that filtering out unplayable clips still fills
    // the feed. Asking for exactly `limit` and then discarding rows
    // would shorten the page every time a provider contributed a few.
    .limit(limit * 2);

  if (error) {
    // Migration 0014 not applied yet. An empty feed and an explanation,
    // never an exception: the homepage renders the news feed too, and one
    // missing video table must not take the whole page with it.
    if (isMissingReelsTable(error)) {
      return { reels: [], error: null, configured: true, note: REELS_NOT_AVAILABLE_NOTE };
    }
    console.error("[reels] feed query failed:", error.message);
    return { reels: [], error: error.message, configured: true };
  }

  return {
    reels: dropRepeatedClips(
      ((data ?? []) as unknown[]).map(mapReelRow).filter(isPlayableReel)
    ).slice(0, limit),
    error: null,
    configured: true,
  };
}

/**
 * The same clip, twice.
 *
 * Two providers can carry the same video - a Wikimedia file that a
 * YouTube upload also has - and one provider can list the same file
 * twice under two ids. The reader would then watch the same clip
 * twice in a row and think the feed had looped.
 *
 * The key is the clip's own ADDRESS: the file the browser will fetch.
 * Two rows that point at the same file are the same clip, whatever
 * their ids say.
 */
export function dropRepeatedClips<T extends { id: string; video_url?: string | null; embed_url?: string | null; thumbnail_url?: string | null }>(
  reels: T[]
): T[] {
  const seen = new Set<string>();
  const kept: T[] = [];

  for (const reel of reels) {
    const key = reel.video_url || reel.embed_url || reel.thumbnail_url;
    if (key) {
      if (seen.has(key)) continue;
      seen.add(key);
    }
    kept.push(reel);
  }

  return kept;
}

/**
 * Drop a clip NO browser will decode.
 *
 * The ingestion pipeline refuses to STORE these, but rows written before
 * that check existed are still in the table — and a `.ogv` renders as
 * "Replay unavailable" with no sound, which reads as a broken feature
 * rather than an old row. The feed is also asked for more rows than it
 * shows, so dropping a few here still fills the screen.
 *
 * Kept deliberately narrow: only formats we know browsers refuse are
 * excluded. Anything unrecognised passes, because a file whose type we
 * failed to guess is far more likely to play than not.
 *
 * WEBM IS NOT HERE, AND THAT IS THE POINT
 * ---------------------------------------
 * A webm row is the live production bug on iPhone, and it is still not
 * filtered, because webm is not dead everywhere — it plays in Chromium
 * and Firefox and in Safari on macOS, so removing those rows here would
 * hide good content from most of the audience to fix one platform.
 *
 * Whether a given reader can decode webm is a fact about their browser,
 * not about the file, so it is asked of the browser after mount rather
 * than guessed here from a request header. See lib/reel-codec.ts. Note
 * also that this module is `server-only`, which is a second reason the
 * question cannot be answered in this function: there is no `document`
 * to ask.
 *
 * For the record, because it looks fixable here and is not: Wikimedia
 * Commons publishes no H.264 mp4 at all — no `.480p.mp4`, and every one
 * of the 184 derivative entries in a 50-file sample of its output was
 * `.vp9.webm`, `.mpeg4.mov` or `.theora.ogv`. Nothing upstream of us can
 * be pointed at an mp4 for this source.
 */
function isPlayableReel(reel: Reel): boolean {
  const url = ((reel.video_url ?? "").toLowerCase().split("?")[0] ?? "");
  // Ogg Video (Theora) plays in no current browser; .ogg is audio.
  if (url.endsWith(".ogv") || url.endsWith(".ogg") || url.endsWith(".theora")) {
    return false;
  }

  // A YouTube embed URL with no companion `embed_url` cannot play at all:
  // `<video>` rejects it and the card is "Replay unavailable". That is
  // what a youtube row looks like before migration 0015 has been
  // applied. Dropping it here turns a grid of broken cards into a feed
  // that is merely shorter, and the YouTube rows come back on their own
  // once the migration lands — nothing has to be re-ingested.
  if (!reel.embed_url && url.includes("youtube.com/embed/")) {
    return false;
  }

  return true;
}

/** Map a raw `reels` row to the shape the feed renders. */
function mapReelRow(row: unknown): Reel {
  const item = row as Record<string, unknown>;
  const videoUrl = String(item.video_url);

  return {
    id: String(item.id),
    // Present since 0014, so no absent-column concern here — but a row
    // written by a hand or an import may still carry a null, and the
    // card renders nothing rather than "null" in that case.
    source_key: (item.source_key as string) ?? null,
    // The only two columns the schema declares NOT NULL, and the player
    // cannot start without them.
    video_url: videoUrl,
    // Absent on every database that has not had 0015 applied, which is
    // the whole point of it being nullable: reading a column that does
    // not exist is a PGRST error, and a column that is there but null is
    // just "this clip is a plain media file".
    embed_url: (item.embed_url as string) ?? null,
    poster_url: wikimediaPosterUrl(
      videoUrl,
      (item.poster_url as string) ?? null
    ),
    title: (item.title as string) ?? null,
    description: (item.description as string) ?? null,
    creator_name: (item.creator_name as string) ?? null,
    creator_url: (item.creator_url as string) ?? null,
    license_name: (item.license_name as string) ?? null,
    license_url: (item.license_url as string) ?? null,
    // Anything that is not an explicit `false` reads as "credit required".
    // Attribution is the default in 0014 for the same reason the UI always
    // renders the credit line: a provider that does not demand credit is
    // not a provider whose work may be presented as ours.
    attribution_required: item.attribution_required !== false,
    width: toNumberOrNull(item.width),
    height: toNumberOrNull(item.height),
    // numeric comes back from PostgREST as a JSON number, but a driver or
    // proxy that stringifies it must not blank the duration readout.
    duration_seconds: toNumberOrNull(item.duration_seconds),
    published_at: (item.published_at as string) ?? null,
    like_count: readLikeCount(item.reel_likes),
  };
}

/**
 * Read the count out of `reel_likes(count)`.
 *
 * PostgREST returns the aggregate as an array of one object per parent
 * row. Anything else — an empty array, a null embed from a missing table,
 * a shape change in a future PostgREST — reads as zero, which is the
 * honest floor for "nobody has liked this yet".
 */
function readLikeCount(embedded: unknown): number {
  if (!Array.isArray(embedded)) return 0;
  const first = embedded[0] as Record<string, unknown> | undefined;
  return toNumberOrNull(first?.count) ?? 0;
}

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A bad or absent limit means "the page size", never "no limit". */
function clampLimit(requested: number | undefined): number {
  const parsed = Number(requested);
  if (!Number.isFinite(parsed) || parsed < 1) return REELS_PAGE_SIZE;
  return Math.min(Math.trunc(parsed), REELS_MAX_LIMIT);
}