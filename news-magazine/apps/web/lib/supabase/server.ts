import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import type { Database } from "@/types/database";

function resolveUrl(): string {
  return (
    process.env.NEXT_PUBLIC_SUPABASE_URL ||
    process.env.SUPABASE_URL ||
    "https://placeholder.supabase.co"
  );
}

function resolveAnonKey(): string {
  return (
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    process.env.SUPABASE_ANON_KEY ||
    "missing-anon-key"
  );
}

/**
 * A fetch that is never cached.
 *
 * Next 14 caches `fetch` GETs by default — the Data Cache — and
 * `export const dynamic = "force-dynamic"` does NOT turn that off. It
 * makes the ROUTE dynamic; each fetch inside it is still free to be
 * cached, keyed by URL.
 *
 * That is silent and severe for a news site. Every distinct query URL
 * is computed once and then served from cache forever, so a homepage
 * asking for 24 articles is pinned to whatever the database held the
 * first time that exact URL was fetched, while the same route asked for
 * 25 returns a different frozen set computed minutes later. The result
 * is a feed whose contents depend on the request rather than the
 * database — which is exactly the "asking for more rows returns older
 * news" symptom, and why it never reproduced locally.
 *
 * It also stranded /category/reels: the categories query was cached
 * before migration 0014 ran, so the row existed in the database and the
 * page still 404'd.
 */

/**
 * How long a public read may be reused before the database is asked again.
 *
 * THE REASON THE READS ARE NOT `no-store` ANY MORE
 * ------------------------------------------------
 * Every public route on this site renders from the database on the
 * request, and the database is in another continent from the server
 * that renders it. Measured against the live site, that is the whole
 * story of the Performance score:
 *
 *   TTFB on the homepage      1.9s
 *   TTFB on a category page   2.9s
 *
 * — all of it spent waiting for PostgREST, before a single byte of
 * HTML was produced. Nothing the page does afterwards can be fast
 * enough to matter while the browser is still waiting for the first
 * byte.
 *
 * A cached read turns that wait into a cache lookup. The entry is
 * keyed by the request URL, exactly as before, and it EXPIRES: a
 * minute-old answer is a minute-old answer, not a frozen one, so the
 * feed still moves and the newest story is never more than a minute
 * away. The old failure mode — an entry that never expired, so the
 * homepage was pinned to the first thing it ever fetched — cannot
 * happen with a revalidate window.
 *
 * Sixty seconds is the trade the site asked for: a one-minute-old
 * cache is fine, a five-minute-old one is not.
 */
export const PUBLIC_READ_REVALIDATE_SECONDS = 60;

/**
 * A fetch whose GETs are cached by Next for `revalidate` seconds.
 *
 * `next: { revalidate }` is the explicit form, and it is explicit on
 * purpose. The IMPLICIT form — a plain GET with no `cache` option —
 * is cached FOREVER on Next 14, which is the bug the comment above
 * describes. An explicit revalidate puts a deadline on every entry.
 *
 * `cache` and `next.revalidate` are mutually exclusive in Next (it
 * warns if both are set), so the incoming `cache` is dropped rather
 * than left to fight with the revalidate.
 *
 * Only GET and HEAD are cached. A write is a write.
 */
function cachedReadFetch(revalidate: number): typeof fetch {
  return (input, init) => {
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      return fetch(input, init);
    }

    const { cache: _cache, ...rest } = init ?? {};
    return fetch(input, {
      ...rest,
      next: { ...(rest.next ?? {}), revalidate },
    });
  };
}

/**
 * A fetch that is never cached.
 *
 * Used where the answer depends on WHO is asking, or where the answer
 * is about the health of the connection rather than about the news.
 */
const noStoreFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, cache: "no-store" });

/**
 * Cookie-less Supabase client for PUBLIC reads.
 *
 * Every page that renders news is reading rows the `articles_public_
 * select_published` RLS policy already exposes to anonymous visitors, so
 * none of them need a session cookie.
 *
 * This matters because `cookies()` throws
 * "cookies was called outside a request scope" during static generation
 * — `generateStaticParams` and `generateMetadata` run at build time,
 * outside any request. Calling the cookie-based client from there broke
 * the whole site with a server error.
 *
 * Using the anon key (never the service-role key) keeps Row Level
 * Security as the authorization boundary.
 *
 * `revalidate` is how long one answer may be reused, in seconds. Zero
 * means never reuse it. The default is the site's news window: a
 * minute-old feed is a feed that is still news.
 */
export function createPublicSupabaseClient(options?: { revalidate?: number }) {
  const revalidate = options?.revalidate ?? PUBLIC_READ_REVALIDATE_SECONDS;
  return createClient<Database>(resolveUrl(), resolveAnonKey(), {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    global: { fetch: revalidate > 0 ? cachedReadFetch(revalidate) : noStoreFetch },
  });
}

/**
 * The SERVICE ROLE client, for the tables the anon key must not read.
 *
 * This bypasses Row Level Security entirely, so it is the most
 * dangerous client in the app and the only one that must never reach
 * the browser. It exists for exactly one thing today: the admin
 * tables, which carry password hashes and therefore have no
 * `using (true)` select policy - an admin's hash must not be
 * readable by anyone who asks for it.
 *
 * It is only ever constructed inside a route handler or a server
 * component. There is no `NEXT_PUBLIC_` prefix on the variable it
 * reads, so Next.js will not inline it into the client bundle.
 */
export function createServiceRoleClient() {
  const url = resolveUrl();
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!key) {
    throw new Error(
      "SUPABASE_SERVICE_ROLE_KEY is not set. The admin panel cannot read " +
        "the admin tables without it.",
    );
  }

  return createClient<Database>(url, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    // No `cache: "no-store"` override here. The service role's reads
    // are of credentials, and a credential that is cached is a
    // credential that outlives the change that revoked it.
  });
}

/**
 * Session-aware Supabase client.
 *
 * Reads the user's session from cookies and still enforces RLS with the
 * anon key — this is NOT the service-role client. Use this for anything
 * that depends on who the user is: auth, admin, comments, bookmarks.
 */
export function createServerSupabaseClient() {
  const cookieStore = cookies();

  return createServerClient<Database>(resolveUrl(), resolveAnonKey(), {
    // Same reason as createPublicSupabaseClient: a cached read is a feed
    // frozen at a moment the reader never chose.
    global: { fetch: noStoreFetch },
    cookies: {
      get(name: string) {
        return cookieStore.get(name)?.value;
      },
      set(name: string, value: string, options: CookieOptions) {
        cookieStore.set({ name, value, ...options });
      },
      remove(name: string, options: CookieOptions) {
        cookieStore.set({ name, value: "", ...options });
      },
    },
  });
}
