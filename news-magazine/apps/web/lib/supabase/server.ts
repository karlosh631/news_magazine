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
 */
export function createPublicSupabaseClient() {
  return createClient<Database>(resolveUrl(), resolveAnonKey(), {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
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
