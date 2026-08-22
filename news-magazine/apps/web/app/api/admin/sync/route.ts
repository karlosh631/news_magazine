import { NextRequest, NextResponse } from "next/server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/**
 * Triggers the Python ingestion service's /api/sync endpoint.
 *
 * Two allowed callers, both authenticated server-side — never trust a
 * client-supplied "admin=true" flag (spec section 24):
 *   1. An authenticated admin/super_admin user via the dashboard.
 *   2. A cron service (e.g. Vercel Cron) presenting CRON_SECRET as a
 *      bearer token, matched against the server-only env var.
 *
 * The actual INGESTION_SECRET used to call the Python service never
 * reaches the browser — it's attached here, server-side, only.
 */
export async function POST(req: NextRequest) {
  const authHeader = req.headers.get("authorization");
  const isCron = authHeader === `Bearer ${process.env.CRON_SECRET}`;

  if (!isCron) {
    const supabase = createServerSupabaseClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ success: false, error: { code: "UNAUTHENTICATED", message: "Sign in required." } }, { status: 401 });
    }

    const { data: roles } = await supabase.from("user_roles").select("role").eq("user_id", user.id);
    const isAdmin = roles?.some((r) => r.role === "admin" || r.role === "super_admin");
    if (!isAdmin) {
      return NextResponse.json({ success: false, error: { code: "FORBIDDEN", message: "Admin role required." } }, { status: 403 });
    }
  }

  const body = await req.json().catch(() => ({}));

  const res = await fetch(`${process.env.INGESTION_SERVICE_URL}/api/sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-ingestion-secret": process.env.INGESTION_SECRET!,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    return NextResponse.json(
      { success: false, error: { code: "INGESTION_SERVICE_ERROR", message: "Sync request failed." } },
      { status: 502 }
    );
  }

  const data = await res.json();
  return NextResponse.json(data);
}
