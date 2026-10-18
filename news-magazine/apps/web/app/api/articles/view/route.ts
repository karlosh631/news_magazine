import { NextResponse } from "next/server";
import { z } from "zod";

import { createServerSupabaseClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const schema = z.object({ articleId: z.string().uuid() });

// Per-process cap so the view counter cannot be used as a write amplifier.
const WINDOW_MS = 60_000;
const MAX = 600;
let hits: number[] = [];

function rateLimited(): boolean {
  const now = Date.now();
  hits = hits.filter((t) => now - t < WINDOW_MS);
  if (hits.length >= MAX) return true;
  hits.push(now);
  return false;
}

/**
 * Record one article view.
 *
 * Moved out of the page's render body into an explicit POST so it runs
 * once per view rather than once per render (see
 * components/ArticleViewTracker.tsx).
 */
export async function POST(request: Request) {
  if (rateLimited()) {
    return NextResponse.json({ ok: false }, { status: 429 });
  }

  const parsed = schema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  try {
    const supabase = createServerSupabaseClient();
    const { error } = await supabase.rpc("increment_article_view", {
      p_article_id: parsed.data.articleId,
    });
    if (error) throw error;
  } catch {
    // A view count is not worth surfacing an error for.
    return NextResponse.json({ ok: false }, { status: 202 });
  }

  return NextResponse.json({ ok: true });
}