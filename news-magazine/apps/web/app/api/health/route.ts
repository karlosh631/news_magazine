import { NextResponse } from "next/server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export async function GET() {
  let dbOk = true;
  try {
    const supabase = createServerSupabaseClient();
    await supabase.from("categories").select("id").limit(1);
  } catch {
    dbOk = false;
  }

  return NextResponse.json({
    status: dbOk ? "ok" : "degraded",
    database: dbOk ? "ok" : "error",
    timestamp: new Date().toISOString(),
  });
}
