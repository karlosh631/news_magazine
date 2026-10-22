import { NextRequest, NextResponse } from "next/server";

import {
  ADMIN_COOKIE,
  adminCookieOptions,
  readSession,
  sessionSecret,
} from "@/lib/admin-auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Sign out.
 *
 * The cookie is cleared AND the session row is deleted, so the session
 * ends immediately rather than when the cookie would have expired.
 * Deleting the row is what makes "sign out everywhere" possible: the
 * row is the authority, and the cookie is only the claim.
 */
export async function POST(request: NextRequest) {
  const secret = sessionSecret();
  const session = await readSession(
    request.cookies.get(ADMIN_COOKIE)?.value,
    secret
  );

  if (session) {
    try {
      const supabase = createServiceRoleClient();
      await supabase.from("admin_sessions").delete().eq("id", session.sid);
    } catch (error) {
      // The cookie is cleared either way, so the reader is signed out
      // on this device even if the row could not be removed.
      console.error("[admin] could not delete the session row:", error);
    }
  }

  const response = NextResponse.json({ success: true });
  response.cookies.set(ADMIN_COOKIE, "", adminCookieOptions(0));

  return response;
}
