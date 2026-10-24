import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Header } from "@/components/Header";
import { RouteFooter } from "@/components/RouteFooter";
import { getCategories } from "@/lib/news";
import { themeScript } from "@/components/ThemeToggle";
import { SITE_NAME, SITE_URL, SITE_LOGO } from "@/lib/site";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} — Live`,
    template: `%s | ${SITE_NAME}`,
  },
  description:
    "Live Nepali and English news from more than 20 outlets across Nepal — national, politics, business, technology, world and sport.",
  applicationName: SITE_NAME,
  keywords: [
    "Nepal news",
    "Nepali news",
    "नेपाली समाचार",
    "OnlineKhabar",
    "Setopati",
    "Kathmandu Post",
    "Rato Pati",
    "Nagarik",
    "breaking news",
    "नेपाल",
  ],
  authors: [{ name: SITE_NAME, url: SITE_URL }],
  creator: SITE_NAME,
  publisher: SITE_NAME,
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    locale: "ne_NP",
    alternateLocale: ["en_US"],
    url: SITE_URL,
    siteName: SITE_NAME,
    title: `${SITE_NAME} — Live`,
    description:
      "Live Nepali and English news from more than 20 outlets across Nepal.",
    images: [
      {
        url: SITE_LOGO,
        width: 192,
        height: 192,
        alt: SITE_NAME,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: `${SITE_NAME} — Live`,
    description:
      "Live Nepali and English news from more than 20 outlets across Nepal.",
    images: [SITE_LOGO],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
  formatDetection: { telephone: false, address: false, email: false },
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "32x32" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180" }],
  },
};

/**
 * Viewport is a separate export in Next 14 — themeColor here used to be
 * silently ignored when declared inside `metadata`, so the browser
 * chrome never matched the site's dark mode.
 */
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#111827" },
  ],
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Navigation comes from the database so adding a category is an
  // admin operation rather than a code change.
  const allCategories = await getCategories();


  // Six in the nav, and that is a space decision rather than a content
  // one: all twelve wrapped onto two lines and broke every word
  // mid-syllable on desktop. Reels is appended last so the five that
  // were already there keep their positions. The other six remain
  // reachable at /category/<slug>; this only limits what is shown.
  const NAV_ORDER = [
    "national",
    "politics",
    "business",
    "technology",
    "world",
    "reels",
    "rashifal",
  ];
  const categories = NAV_ORDER.map((slug) => allCategories.find((c) => c.slug === slug))
    .filter((c): c is NonNullable<typeof c> => Boolean(c))
    .map((c) => ({ slug: c.slug, name_en: c.name_en, name_ne: c.name_ne }));

  return (
    // The reader's chosen language is applied to <html lang> by the
    // header on mount. It is declared "ne" here because the headlines
    // this site serves are predominantly Nepali; saying otherwise on
    // the server would contradict the content for every crawler that
    // does not run scripts.
    <html lang="ne" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint, so a dark-mode
            reader never sees a white flash. */}
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="flex min-h-screen flex-col bg-white text-gray-900 antialiased font-body">
        <Header categories={categories} />
        {/*
          No <main> wrapper here. Every page renders its own <main>,
          and a <main> nested inside another <main> is invalid HTML —
          screen readers announce two main landmarks per page.
        */}
        <div className="flex-1">{children}</div>
        <RouteFooter />
      </body>
    </html>
  );
}