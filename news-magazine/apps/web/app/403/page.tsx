export const dynamic = "force-dynamic";

import Link from "next/link";

/**
 * Not allowed.
 *
 * The middleware redirects here when a signed-in user has no admin
 * role, and there was no such page - so the one case where the
 * answer is "you are known, and the answer is no" produced a 404 that
 * looked like a broken link rather than a decision.
 *
 * The page says which of the two things happened - signed in but not
 * an administrator, or not signed in at all - because the fix is
 * different in each case, and a reader who is told "sign in" when
 * they already are will try the same thing again.
 */
export default function ForbiddenPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-12 dark:bg-gray-950">
      <div className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-8 text-center shadow-sm dark:border-gray-700 dark:bg-gray-900">
        <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
          403
        </p>
        <h1 className="mt-2 text-xl font-bold text-gray-900 dark:text-gray-100">
          You do not have access to this
        </h1>
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">
          This page is for administrators. If that is you and you are
          seeing this, sign in with the administrator account — it is a
          different sign-in from the one you used to read the site.
        </p>
        <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/login?redirect=/admin"
            className="w-full rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-black focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-900 focus-visible:ring-offset-2 sm:w-auto dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white dark:focus-visible:ring-gray-100 dark:focus-visible:ring-offset-gray-900"
          >
            Sign in
          </Link>
          <Link
            href="/"
            className="w-full rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-600 sm:w-auto dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            Back to the news
          </Link>
        </div>
      </div>
    </main>
  );
}
