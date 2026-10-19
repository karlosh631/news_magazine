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

/** A vertical feed asks for far fewer clips than the news feed asks for stories. */
export const REELS_PAGE_SIZE = 12;

/** Hard ceiling, so a bad caller cannot ask for the whole table. */
const REELS_MAX_LIMIT = 50;

/**
 * Shown whenever the reels tables are not there yet.
 *
 * Migration 0014 is additive and idempotent, so a database that has not
 * had it applied is a perfectly healthy database — it just has no Reels
 * feature yet. That is not an error and must never render as one.
 */
export const REELS_NOT_AVAILABLE_NOTE =
  "Reels are not available yet: apply supabase/migrations/0014_reels.sql to create the reels and reel_likes tables.";

export interface Reel {
  id: string;
  video_url: string;
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
  return createPublicSupabaseClient() as unknown as SupabaseClient;
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
 * Nothing caches this. There is no `revalidate`, no `unstable_cache`, no
 * `next: { revalidate }` on the request — a like or a fresh ingest should
 * be visible on the very next render, and a stale like count under a
 * thumb is a lie the reader can see.
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
      "id, video_url, poster_url, title, description, creator_name, creator_url, license_name, license_url, attribution_required, width, height, duration_seconds, published_at, reel_likes(count)"
    )
    // nullsFirst: false is required, not cosmetic — the same trap the
    // articles feed documents. PostgreSQL defaults `ORDER BY ... DESC` to
    // NULLS FIRST, so an undated clip would sit at position 1 and push
    // every dated one down the feed.
    .order("published_at", { ascending: false, nullsFirst: false })
    // Providers publish at whole-second resolution, so two clips routinely
    // share a published_at. Break the tie on when we ingested them, which
    // is monotonic, so the order never shuffles between renders.
    .order("ingested_at", { ascending: false })
    .limit(limit);

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
    reels: ((data ?? []) as unknown[]).map(mapReelRow),
    error: null,
    configured: true,
  };
}

/** Map a raw `reels` row to the shape the feed renders. */
function mapReelRow(row: unknown): Reel {
  const item = row as Record<string, unknown>;

  return {
    id: String(item.id),
    // The only two columns the schema declares NOT NULL, and the player
    // cannot start without them.
    video_url: String(item.video_url),
    poster_url: (item.poster_url as string) ?? null,
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