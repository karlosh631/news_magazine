import { NextResponse } from "next/server";

import { createPublicSupabaseClient } from "@/lib/supabase/server";

/**
 * Where a device's push subscription is kept.
 *
 * WHAT THIS IS FOR
 * ----------------
 * A browser that is going to be told about the news while it is
 * closed has to be reachable: the server needs the address the push
 * service will deliver to. That address is a subscription, and this
 * route is where it is kept.
 *
 * The table is `push_subscriptions` (migration 0035). When it has not
 * been applied the route says so, plainly, and the reader's
 * notification - the one the browser raises from the page itself -
 * keeps working. The two are independent: the local notification
 * needs nothing stored here at all.
 */
export const dynamic = "force-dynamic";

const NO_STORE = "no-store, no-cache, must-revalidate, max-age=0";

interface Subscription {
  endpoint?: unknown;
  keys?: { p256dh?: unknown; auth?: unknown } | unknown;
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text ? text : null;
}

export async function POST(request: Request) {
  let body: Subscription | null = null;
  try {
    body = (await request.json()) as Subscription;
  } catch {
    return NextResponse.json(
      { error: "The subscription could not be read." },
      { status: 400, headers: { "Cache-Control": NO_STORE } }
    );
  }

  const endpoint = asString(body?.endpoint);
  const keys = (body?.keys ?? {}) as { p256dh?: unknown; auth?: unknown };
  const p256dh = asString(keys.p256dh);
  const auth = asString(keys.auth);

  if (!endpoint || !p256dh || !auth) {
    return NextResponse.json(
      { error: "A subscription needs an endpoint and both keys." },
      { status: 400, headers: { "Cache-Control": NO_STORE } }
    );
  }

  try {
    const supabase = createPublicSupabaseClient();
    const { error } = await supabase.from("push_subscriptions").upsert(
      {
        endpoint,
        p256dh,
        auth,
        user_agent: request.headers.get("user-agent") ?? null,
        last_seen_at: new Date().toISOString(),
      },
      { onConflict: "endpoint" }
    );

    if (error) {
      // The table is the likely reason: the migration is the
      // operator's to apply, and saying so is more useful than a 500.
      return NextResponse.json(
        {
          error:
            "The subscription could not be stored. The push_subscriptions table may not have been created yet.",
        },
        { status: 503, headers: { "Cache-Control": NO_STORE } }
      );
    }

    return NextResponse.json(
      { ok: true },
      { headers: { "Cache-Control": NO_STORE } }
    );
  } catch (error) {
    return NextResponse.json(
      { error: "The subscription could not be stored." },
      { status: 503, headers: { "Cache-Control": NO_STORE } }
    );
  }
}
