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
