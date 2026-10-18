import { NextResponse } from "next/server";
import { z } from "zod";

import { searchArticles } from "@/lib/news";

/**
 * Search.
 *
 * Searches the real articles table via the search_articles() RPC
 * (tsvector + pg_trgm, so Devanagari queries work).
 *
 * Stage 1's version loaded the newest 100 rows into memory, did a
 * substring `.includes()`, and — when nothing matched — silently returned
 * the 8 most recent articles regardless of what was searched for, then
 * asked Gemini to summarise them as if on-topic. Searching "tesla"
 * returned unrelated Nepali politics. It also spent a paid Gemini call on
 * every unauthenticated request with no rate limiting.
 */

export const dynamic = "force-dynamic";

const requestSchema = z.object({
  query: z.string().trim().min(1).max(200),
  limit: z.number().int().min(1).max(50).default(24),
});

// Per-process token bucket. Enough to stop one misbehaving client from
// hammering the database; a real distributed limit belongs at the edge.
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 120;
let hits: number[] = [];

function rateLimited(): boolean {
  const now = Date.now();
  hits = hits.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  if (hits.length >= RATE_LIMIT_MAX) return true;
  hits.push(now);
  return false;
}

export async function POST(request: Request) {
  if (rateLimited()) {
    return NextResponse.json(
      { error: "Too many searches. Try again in a moment." },
      { status: 429 }
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = { query: new URL(request.url).searchParams.get("q") ?? "" };
  }

  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Enter at least one search character." },
      { status: 400 }
    );
  }

  const result = await searchArticles(parsed.data.query, parsed.data.limit);

  if (result.error) {
    return NextResponse.json(
      { error: result.error, articles: [] },
      { status: 503 }
    );
  }

  return NextResponse.json({
    query: parsed.data.query,
    articles: result.articles,
    count: result.articles.length,
  });
}

/** GET is a convenience for links and debugging; same behaviour as POST. */
export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q") ?? "";
  const limit = Number(new URL(request.url).searchParams.get("limit") ?? 24) || 24;
  return POST(
    new Request(request.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, limit }),
    })
  );
}