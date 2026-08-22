import { createBrowserClient } from "@supabase/ssr";
import type { Database } from "@/types/database";

/**
 * Client-side Supabase instance. Uses ONLY the public anon key, which is
 * safe to ship to the browser because every table is protected by Row
 * Level Security (see supabase/migrations/0002_rls_policies.sql). This
 * client can never see or write anything the signed-in user's role
 * doesn't permit.
 */
export function createClient() {
  return createBrowserClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
