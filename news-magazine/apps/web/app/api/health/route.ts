import { NextResponse } from "next/server";
import { createPublicSupabaseClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Liveness, honestly reported.
 *
 * supabase-js does NOT throw on a failed query — it resolves to
 * { data: null, error }. The original check wrapped the await in
 * try/catch, which catches nothing, so it reported "ok" while Supabase
 * was unreachable. The error object has to be inspected explicitly.
 *
 * Diagnostics (which project this deployment is pointed at, how old the
 * newest article is) are NOT public. They are gated behind the shared
 * secret because they name internal infrastructure, and a health
 * endpoint is exactly the kind of thing that gets probed by anyone who
 * finds the URL. Public responses carry no host, no error text, and no
 * row counts — just liveness.
 */
export async function GET(request: Request) {
  const supabase = createPublicSupabaseClient();
  const probe = await supabase.from("categories").select("id").limit(1);

  if (probe.error) {
    return NextResponse.json(
      { status: "degraded", database: "error", timestamp: new Date().toISOString() },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }

  const body: Record<string, unknown> = {
    status: "ok",
    database: "ok",
    timestamp: new Date().toISOString(),
  };

  // Diagnostics only for a caller holding the shared secret. A wrong or
  // missing secret is simply an ordinary public liveness response.
  const provided = request.headers.get("x-diagnostic-secret");
  const expected =
    process.env.INGESTION_SECRET ?? process.env.CRON_SECRET ?? "";

  if (expected && provided && provided.length === expected.length &&
      provided === expected) {
    const configuredUrl =
      process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || "";

    let host = "unconfigured";
    try {
      host = new URL(configuredUrl).host;
    } catch {
      host = "unparseable";
    }

    const newest = await supabase
      .from("articles")
      .select("published_at")
      .eq("status", "published")
      .is("duplicate_of", null)
      .order("published_at", { ascending: false, nullsFirst: false })
      .limit(1);

    const publishedAt =
      (newest.data?.[0]?.published_at as string | undefined) ?? null;

    body.diagnostics = {
      supabase_host: host,
      newest_article: publishedAt,
      newest_age_minutes:
        publishedAt === null
          ? null
          : Math.round((Date.now() - Date.parse(publishedAt)) / 60000),
      newest_query_error: newest.error ? newest.error.message : null,
    };
  }

  return NextResponse.json(body, {
    headers: { "Cache-Control": "no-store" },
  });
}