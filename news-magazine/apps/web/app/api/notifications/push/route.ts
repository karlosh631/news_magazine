import { NextResponse } from "next/server";

import { createServiceRoleClient } from "@/lib/supabase/server";

/**
 * Raise a notification on every device that asked for one.
 *
 * WHO CALLS IT
 * ------------
 * The ingestion worker, at the end of a pass that stored new stories.
 * It knows what is new; this route knows who is listening; the push
 * service is what actually delivers.
 *
 * It is authenticated with the same secret the ingestion worker
 * already has, so nothing new has to be configured for it to be
 * called.
 *
 * WHAT IT NEEDS
 * -------------
 * A VAPID key pair (the push service's credentials) and the
 * `web-push` package. Without them the route says so and does
 * nothing, and the reader's own notification - the one the browser
 * raises from the page - carries on working, because the two are
 * independent.
 */
export const dynamic = "force-dynamic";

const NO_STORE = "no-store, no-cache, must-revalidate, max-age=0";

interface StoredSubscription {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
}

/**
 * The shape of the push library, as far as this route uses it.
 */
interface WebPushLike {
  setVapidDetails(subject: string, publicKey: string, privateKey: string): void;
  sendNotification(
    subscription: unknown,
    payload?: string
  ): Promise<unknown>;
}

/**
 * The push library, if it is installed.
 *
 * It is loaded with `createRequire` rather than an import, so the
 * build does not need it: the site builds and runs whether or not the
 * package is there, and a missing optional dependency must not take
 * the site down.
 */
async function loadWebPush(): Promise<WebPushLike | null> {
  try {
    const { createRequire } = (await import("node:module")) as unknown as {
      createRequire: (path: string) => (id: string) => unknown;
    };
    const require_ = createRequire(import.meta.url);
    // `module` is a reserved name in the Next.js compiler, so the
    // library is held in a differently named variable.
    const library = require_("web-push") as WebPushLike & {
      default?: WebPushLike;
    };
    return library.default ?? library;
  } catch {
    return null;
  }
}

export async function POST(request: Request) {
  // ---- 1. Who is calling? ----------------------------------------
  //
  // The worker and the site already share two secrets, and the site
  // does not necessarily have both of them: the deployment may have
  // one and the other only on the worker. So the caller presents both
  // and either one is enough. This is the same rule the health route
  // uses, for the same reason.
  const presented = [
    request.headers.get("x-ingestion-secret") ?? "",
    request.headers.get("x-cron-secret") ?? "",
  ].filter(Boolean);
  const expected = [
    process.env.INGESTION_SECRET ?? "",
    process.env.CRON_SECRET ?? "",
  ].filter(Boolean);

  const authorised =
    expected.length > 0 && presented.some((value) => expected.includes(value));

  if (!authorised) {
    return NextResponse.json(
      { error: "Not authorised." },
      { status: 401, headers: { "Cache-Control": NO_STORE } }
    );
  }

  // ---- 2. What is new? --------------------------------------------
  let title = "नयाँ समाचार";
  let body = "";
  let url = "/";
  try {
    const payload = (await request.json()) as {
      title?: string;
      body?: string;
      url?: string;
    };
    if (payload.title) title = payload.title;
    if (payload.body) body = payload.body;
    if (payload.url) url = payload.url;
  } catch {
    // A ping with no body is a perfectly good ping.
  }

  // ---- 3. The story the notification is about ---------------------
  //
  // The pass says "there is news" and the site says what it is: the
  // newest story it holds. The site holds the headline, the slug and
  // the picture, and the worker does not, so the site is the right
  // place to ask.
  if (!title || title === "नयाँ समाचार") {
    try {
      const supabase = createServiceRoleClient();
      const { data } = await supabase
        .from("articles")
        .select("headline, slug")
        .eq("status", "published")
        .is("duplicate_of", null)
        .order("published_at", { ascending: false, nullsFirst: false })
        .limit(1);
      const row = (data ?? [])[0] as
        | { headline?: string; slug?: string }
        | undefined;
      if (row?.headline) title = row.headline;
      if (row?.slug) url = `/article/${row.slug}`;
    } catch {
      // The generic title is still a notification.
    }
  }

  // ---- 4. Is the push path configured? ----------------------------
  const publicKey = process.env.VAPID_PUBLIC_KEY ?? "";
  const privateKey = process.env.VAPID_PRIVATE_KEY ?? "";
  const subject = process.env.VAPID_SUBJECT ?? "mailto:news@example.com";

  const webPush = await loadWebPush();
  if (!webPush || !publicKey || !privateKey) {
    return NextResponse.json(
      {
        ok: false,
        configured: false,
        error:
          "The push path is not configured: the web-push package and the VAPID keys are both needed. The reader's own notification still works.",
      },
      { status: 200, headers: { "Cache-Control": NO_STORE } }
    );
  }

  webPush.setVapidDetails(subject, publicKey, privateKey);

  // ---- 5. Who is listening? --------------------------------------
  let subscriptions: StoredSubscription[] = [];
  try {
    const supabase = createServiceRoleClient();
    const { data, error } = await supabase
      .from("push_subscriptions")
      .select("id, endpoint, p256dh, auth");
    if (!error) {
      subscriptions = ((data ?? []) as unknown[]).map((raw) => {
        const row = raw as Record<string, unknown>;
        return {
          id: String(row.id ?? ""),
          endpoint: String(row.endpoint ?? ""),
          p256dh: String(row.p256dh ?? ""),
          auth: String(row.auth ?? ""),
        };
      });
    }
  } catch {
    subscriptions = [];
  }

  if (subscriptions.length === 0) {
    return NextResponse.json(
      { ok: true, configured: true, sent: 0, removed: 0 },
      { headers: { "Cache-Control": NO_STORE } }
    );
  }

  // ---- 6. Send. ---------------------------------------------------
  let sent = 0;
  const dead: string[] = [];

  await Promise.all(
    subscriptions.map(async (subscription) => {
      try {
        await webPush.sendNotification(
          {
            endpoint: subscription.endpoint,
            keys: { p256dh: subscription.p256dh, auth: subscription.auth },
          },
          JSON.stringify({ title, body, url })
        );
        sent += 1;
      } catch (error) {
        const status = (error as { statusCode?: number })?.statusCode;
        // 404 and 410 are the push service saying that this browser
        // will never receive anything again. The row is a dead
        // address and is removed.
        if (status === 404 || status === 410) {
          dead.push(subscription.id);
        }
      }
    })
  );

  if (dead.length > 0) {
    try {
      const supabase = createServiceRoleClient();
      await supabase.from("push_subscriptions").delete().in("id", dead);
    } catch {
      // The rows will be pruned by age instead.
    }
  }

  return NextResponse.json(
    { ok: true, configured: true, sent, removed: dead.length },
    { headers: { "Cache-Control": NO_STORE } }
  );
}
