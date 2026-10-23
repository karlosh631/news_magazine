import type { Metadata } from "next";

import { LoginForm } from "@/components/admin/LoginForm";
import { SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";

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
 *
 * THE PAGE DESCRIBES ITSELF
 * -------------------------
 * The page had no metadata at all, so it inherited the layout's:
 * the title of the HOMEPAGE, the description of the HOMEPAGE, and -
 * because the layout declares `alternates.canonical: "/"` - a
 * canonical URL pointing at the homepage.
 *
 * That last one is the one that matters. A canonical link says "the
 * real version of this page is over there". Telling a crawler that
 * the sign-in page is a copy of the homepage tells it to drop the
 * sign-in page from the index and to count the homepage's content
 * twice. So this page now says what it is.
 */
export const metadata: Metadata = {
  title: "Sign in",
  description:
    "Sign in to the admin dashboard of Nepal News & Magazine.",
  alternates: { canonical: "/login" },
  // The sign-in page is a real page and it is indexable - it is the
  // way to the operator's dashboard - but it says so plainly rather
  // than inheriting a robots directive meant for the news.
  robots: { index: true, follow: true },
};

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
      {/*
        What this page IS, for a machine.

        The other pages on the site describe themselves in JSON-LD: a
        category page says it is a collection of stories, an article
        page says it is a news article. This one had nothing, which
        left it the only page on the site that a crawler could not
        place.

        It is a WebPage: the page whose title is "Sign in", at this
        site's own address, part of this site. jsonLd() escapes `<`,
        so nothing in the values can close the script element early.
      */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: jsonLd({
            "@context": "https://schema.org",
            "@type": "WebPage",
            name: "Sign in",
            description: "Sign in to the admin dashboard of Nepal News & Magazine.",
            url: `${SITE_URL}/login`,
            isPartOf: {
              "@type": "WebSite",
              name: SITE_NAME,
              url: SITE_URL,
            },
          }),
        }}
      />
      <LoginForm redirect={safeRedirect(searchParams.redirect)} />
    </main>
  );
}
