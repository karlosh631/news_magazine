import { createHash } from "node:crypto";

import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { z } from "zod";

import { isSupabaseConfigured } from "@/lib/news";
import {
  REELS_NOT_AVAILABLE_NOTE,
  createReelsClient,
  isMissingReelsTable,
} from "@/lib/reels";

export const dynamic = "force-dynamic";

/**
 * Like and unlike a reel.
 *
 * A like is anonymous. It is keyed on a salted hash of the visitor's IP
 * and user agent, which is enough to make a like idempotent per person
 * and enough to stop one visitor scripting thousands of them — while
 * storing no address and no identifier that could be walked back to one.
 *
 * The write goes through the anonymous client on purpose. Migration 0014
 * grants public select on `reels` and visitor-scoped insert/delete on
 * `reel_likes`, so Row Level Security stays the authorization boundary
 * and the service-role key never touches a public interaction.
 */

const schema = z.object({
  reel_id: z.string().uuid(),
  liked: z.boolean(),
});

/**
 * REEL_LIKE_SALT is required in production.
 *
 * Without a salt the fingerprint is just sha256("ip|user-agent"), and
 * both inputs are things an attacker can guess for any visitor they care
 * to impersonate — a fixed salt would make every fingerprint on the
 * internet reproducible offline from a short list of addresses. Set
 * REEL_LIKE_SALT in the deployment environment. The constant below only
 * exists so a fresh checkout likes reels before anyone has configured
 * anything; changing it later simply re-fingerprints everyone, which
 * makes the old likes unattributable rather than incorrect.
 */
const LIKE_SALT = process.env.REEL_LIKE_SALT || "news-magazine-dev-reel-like-salt";

/** Per-visitor cap so the like button cannot be used as a write amplifier. */
const WINDOW_MS = 60_000;
const MAX_PER_WINDOW = 30;
/** Upper bound on remembered visitors, so the map cannot grow forever. */
const MAX_TRACKED = 10_000;

const hits = new Map<string, number[]>();

export async function POST(request: Request) {
  const parsed = schema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Expected a body of { reel_id: <uuid>, liked: <boolean> }." },
      { status: 400 }
    );
  }

  const { reel_id: reelId, liked } = parsed.data;
  const fingerprint = fingerprintFor(request);

  if (rateLimited(fingerprint)) {
    // Only the hash is ever logged. The IP and user agent that produced it
    // stay inside this function.
    console.warn(`[reels] like rate limit hit for ${fingerprint.slice(0, 12)}`);
    return NextResponse.json({ error: "Too many requests." }, { status: 429 });
  }

  // Nothing is configured in a fresh checkout. That is not a failure the
  // visitor can act on, so it answers 200 with a note and the button
  // simply does nothing.
  if (!isSupabaseConfigured()) {
    return NextResponse.json({
      reel_id: reelId,
      like_count: 0,
      liked,
      note: "Supabase is not configured.",
    });
  }

  const supabase = createReelsClient();

  const writeError = liked
    ? await likeReel(supabase, reelId, fingerprint)
    : await unlikeReel(supabase, reelId, fingerprint);

  if (writeError) {
    // Migration 0014 is missing or partial. 200 with a note, not a 500:
    // the visitor asked to like a reel, and a feature that has not been
    // deployed yet is not an error the reader caused.
    if (isMissingReelsTable(writeError)) {
      return NextResponse.json({
        reel_id: reelId,
        like_count: 0,
        liked,
        note: REELS_NOT_AVAILABLE_NOTE,
      });
    }
    console.error("[reels] like write failed:", writeError.message);
    return NextResponse.json({ error: writeError.message }, { status: 500 });
  }

  // Read the count back rather than incrementing it locally. Two taps from
  // the same visitor, or a script that fires both branches, would otherwise
  // leave the button claiming a number the table does not hold.
  const { count, error: countError } = await supabase
    .from("reel_likes")
    .select("reel_id", { count: "exact", head: true })
    .eq("reel_id", reelId);

  if (countError) {
    if (isMissingReelsTable(countError)) {
      return NextResponse.json({
        reel_id: reelId,
        like_count: 0,
        liked,
        note: REELS_NOT_AVAILABLE_NOTE,
      });
    }
    console.error("[reels] like count failed:", countError.message);
    return NextResponse.json({ error: countError.message }, { status: 500 });
  }

  return NextResponse.json({
    reel_id: reelId,
    like_count: Number(count ?? 0),
    liked,
  });
}

/**
 * INSERT ... ON CONFLICT DO NOTHING, as an upsert.
 *
 * `ignoreDuplicates` makes PostgREST swallow the (reel_id, fingerprint)
 * primary-key collision instead of returning it as a 23505, so liking
 * twice is the same as liking once and needs no read-then-write race of
 * its own.
 */
async function likeReel(
  supabase: SupabaseClient,
  reelId: string,
  fingerprint: string
): Promise<PostgrestError | null> {
  const { error } = await supabase
    .from("reel_likes")
    .upsert(
      { reel_id: reelId, fingerprint },
      { onConflict: "reel_id,fingerprint", ignoreDuplicates: true }
    );

  return error;
}

/** DELETE scoped to this visitor's own row, and only theirs. */
async function unlikeReel(
  supabase: SupabaseClient,
  reelId: string,
  fingerprint: string
): Promise<PostgrestError | null> {
  const { error } = await supabase
    .from("reel_likes")
    .delete()
    .eq("reel_id", reelId)
    .eq("fingerprint", fingerprint);

  return error;
}

/**
 * A salted hash of the visitor's address and user agent.
 *
 * The raw values are read, hashed and dropped: they are never stored and
 * never logged, and the database only ever sees the digest. A visitor
 * behind no proxy at all still gets a stable fingerprint from their user
 * agent alone.
 */
function fingerprintFor(request: Request): string {
  // x-forwarded-for is a comma-separated chain appended to by each hop;
  // the left-most entry is the original client.
  const forwarded = request.headers.get("x-forwarded-for");
  const ip = (
    forwarded?.split(",")[0] ??
    request.headers.get("x-real-ip") ??
    ""
  ).trim();
  const userAgent = request.headers.get("user-agent") ?? "";

  return createHash("sha256")
    .update(`${LIKE_SALT}|${ip}|${userAgent}`)
    .digest("hex");
}

/** Sliding one-minute window, counted per fingerprint. */
function rateLimited(fingerprint: string): boolean {
  const now = Date.now();
  const recent = (hits.get(fingerprint) ?? []).filter((t) => now - t < WINDOW_MS);

  if (recent.length >= MAX_PER_WINDOW) {
    hits.set(fingerprint, recent);
    return true;
  }

  recent.push(now);
  hits.set(fingerprint, recent);

  // Drop visitors who have not come back in a minute. Without this the map
  // only ever grows, and on a long-lived server process that is a slow leak
  // of one entry per distinct visitor.
  if (hits.size > MAX_TRACKED) {
    for (const [key, stamps] of hits) {
      if (stamps.every((t) => now - t >= WINDOW_MS)) hits.delete(key);
    }
  }

  return false;
}