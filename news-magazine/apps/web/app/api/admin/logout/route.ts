import { NextRequest, NextResponse } from "next/server";

import {
  ADMIN_COOKIE,
  adminCookieOptions,
} from "@/lib/admin-auth";
import { checkAdminSession } from "@/lib/admin-session";
import { crossSiteReason, isSameOriginRequest } from "@/lib/same-origin";
import { createServiceRoleClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Sign out.
 *
 * The cookie is cleared AND the session row is deleted, so the session
 * ends immediately rather than when the cookie would have expired.
 * Deleting the row is what makes "sign out everywhere" possible: the
 * row is the authority, and the cookie is only the claim.
 *
 * The request has to come from this site. A sign-out is not a
 * dangerous thing to do to somebody - it is an annoyance, and an
 * annoyance is still worth not being able to cause from another
 * site - and the check costs one header comparison.
 */
export async function POST(request: NextRequest) {
  if (!isSameOriginRequest(request.headers)) {
    console.warn(
      `[admin] refused a sign-out from another site (${crossSiteReason(
        request.headers
      )})`
    );
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "CROSS_SITE_REQUEST",
          message: "This sign-out request did not come from this site.",
        },
      },
      { status: 403 },
    );
  }

  const check = await checkAdminSession(request.cookies.get(ADMIN_COOKIE)?.value);

  if (check.ok) {
    try {
      const supabase = createServiceRoleClient();
      await supabase.from("admin_sessions").delete().eq("id", check.session.sid);
    } catch (error) {
      // The cookie is cleared either way, so the reader is signed out
      // on this device even if the row could not be removed.
      console.error("[admin] could not delete the session row:", error);
    }
  } else if (check.reason === "lookup-failed") {
    // The row could not be read, so it could not be deleted. The
    // cookie is cleared anyway, and the session dies when it expires.
    console.error("[admin] the session could not be checked before signing out");
  }

  const response = NextResponse.json({ success: true });
  response.cookies.set(ADMIN_COOKIE, "", adminCookieOptions(0));

  return response;
}
