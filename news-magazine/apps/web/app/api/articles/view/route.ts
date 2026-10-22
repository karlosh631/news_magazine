import { NextResponse } from "next/server";
import { z } from "zod";

import { recordEvent } from "@/lib/analytics-events";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const schema = z.object({
  articleId: z.string().uuid(),
  // The reader's session id, so "215 page views" can be read as
  // "18 readers". Optional: a view without one is still a view.
  sessionId: z.string().max(120).optional().nullable(),
  referrer: z.string().max(500).optional().nullable(),
});

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
      p_referrer: parsed.data.referrer ?? null,
      p_session_id: parsed.data.sessionId ?? null,
    });
    if (error) throw error;

    /*
      The reader-event row.

      The `analytics_events` table had an insert-only policy, an index
      and a panel on the dashboard, and NOTHING EVER WROTE TO IT. So
      the "Reader events" panel said "No reader events recorded yet",
      and it would have said it for ever, and the operator would have
      concluded that nobody was reading the site - while the view
      counter beside it said 215.

      The view count and the event are different facts. The count is a
      number on the article; the event is a thing that happened, with
      a type and a time, and it is what the event mix and the
      timeline are built from.
    */
    recordEvent("view", {
      articleId: parsed.data.articleId,
      sessionId: parsed.data.sessionId ?? null,
      metadata: parsed.data.referrer
        ? { referrer: parsed.data.referrer }
        : null,
    });
  } catch {
    // A view count is not worth surfacing an error for.
    return NextResponse.json({ ok: false }, { status: 202 });
  }

  return NextResponse.json({ ok: true });
}