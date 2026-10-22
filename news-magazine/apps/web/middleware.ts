import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

import { ADMIN_COOKIE, readSession, sessionSecret } from "@/lib/admin-auth";

/**
 * Server-side gate for every /admin/* route. This runs before any admin
 * page renders — role is always re-checked against the database via the
 * user's session, never inferred from anything the client sent (spec
 * section 24: "Never trust role ... from frontend requests").
 *
 * TWO DOORS
 * ----------
 * 1. The Supabase Auth path, which is what this always did: a session
 *    plus a `user_roles` row.
 * 2. The admin-cookie path, for an operator who should not have to
 *    open a Supabase dashboard to use their own site. The cookie
 *    carries a signed session id, and the id is checked against the
 *    `admin_sessions` table by the page that renders the dashboard.
 *
 * The middleware only establishes that a session is PLAUSIBLE — the
 * cookie is signed and unexpired. The page checks the row, so a
 * revoked session stops working at the point where it matters.
 */
export async function middleware(request: NextRequest) {
  const response = NextResponse.next();

  if (!request.nextUrl.pathname.startsWith("/admin")) {
    return response;
  }

  // Door 2: the signed admin cookie. Checked first because it is the
  // cheaper of the two — no network call to Supabase's auth server.
  const secret = sessionSecret();
  const adminSession = await readSession(
    request.cookies.get(ADMIN_COOKIE)?.value,
    secret
  );

  if (adminSession) {
    return response;
  }

  const supabaseUrl =
    process.env.NEXT_PUBLIC_SUPABASE_URL ||
    process.env.SUPABASE_URL ||
    "https://placeholder.supabase.co";
  const supabaseAnonKey =
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    process.env.SUPABASE_ANON_KEY ||
    "missing-anon-key";

  const supabase = createServerClient(
    supabaseUrl,
    supabaseAnonKey,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (cookies) => {
          cookies.forEach(({ name, value, options }) => {
            response.cookies.set({ name, value, ...options });
          });
        },
      },
    }
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.redirect(new URL("/login?redirect=/admin", request.url));
  }

  const { data: roles } = await supabase.from("user_roles").select("role").eq("user_id", user.id);
  const allowed = new Set(["admin", "super_admin", "editor", "moderator", "journalist"]);
  const hasAdminAccess = roles?.some((r) => allowed.has(r.role));

  if (!hasAdminAccess) {
    return NextResponse.redirect(new URL("/403", request.url));
  }

  return response;
}

export const config = {
  matcher: "/admin/:path*",
};
