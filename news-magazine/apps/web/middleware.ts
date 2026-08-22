import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

/**
 * Server-side gate for every /admin/* route. This runs before any admin
 * page renders — role is always re-checked against the database via the
 * user's session, never inferred from anything the client sent (spec
 * section 24: "Never trust role ... from frontend requests").
 */
export async function middleware(request: NextRequest) {
  const response = NextResponse.next();

  if (!request.nextUrl.pathname.startsWith("/admin")) {
    return response;
  }

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get: (name) => request.cookies.get(name)?.value,
        set: (name, value, options) => response.cookies.set({ name, value, ...options }),
        remove: (name, options) => response.cookies.set({ name, value: "", ...options }),
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
