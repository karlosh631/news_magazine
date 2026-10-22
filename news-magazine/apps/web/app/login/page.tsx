import { LoginForm } from "@/components/admin/LoginForm";

export const dynamic = "force-dynamic";

/**
 * The sign-in page.
 *
 * WHY IT EXISTS
 * -------------
 * `middleware.ts` redirected unauthenticated /admin requests to
 * `/login?redirect=/admin`, and there was no such page - so the
 * operator who clicked the admin link got a 404 with a URL that
 * looked like it was working. A redirect to a page that does not
 * exist is worse than no redirect at all, because it looks like a
 * permissions problem rather than a missing page.
 *
 * THE REDIRECT TARGET IS CHECKED HERE
 * -----------------------------------
 * The `redirect` parameter is read on the server and reduced to a
 * path on THIS site before it is ever handed to the form. An absolute
 * URL, a protocol-relative one, or anything that does not start with
 * a single slash would turn the login form into an open redirect - a
 * link that looks like ours and lands the reader somewhere else,
 * having just typed their password into it.
 */

function safeRedirect(target: string | string[] | undefined): string {
  const value = Array.isArray(target) ? target[0] : target;
  if (!value) return "/admin";
  if (!value.startsWith("/")) return "/admin";
  // "//evil.example" is protocol-relative, not a local path.
  if (value.startsWith("//")) return "/admin";
  return value;
}

export default function LoginPage({
  searchParams,
}: {
  searchParams: { [key: string]: string | string[] | undefined };
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-12 dark:bg-gray-950">
      <LoginForm redirect={safeRedirect(searchParams.redirect)} />
    </main>
  );
}
