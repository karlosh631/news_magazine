import type { Metadata } from "next";
import "./globals.css";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { getCategories } from "@/lib/news";
import { themeScript } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: {
    default: "Nepal News & Magazine",
    template: "%s | Nepal News & Magazine",
  },
  description: "Latest Nepali and English news, opinion, and features.",
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "https://new24hr.vercel.app"
  ),
  openGraph: { type: "website", locale: "ne_NP" },
  manifest: "/manifest.json",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Navigation comes from the database so adding a category is an
  // admin operation rather than a code change.
  const allCategories = await getCategories();

  // Five in the nav. Eleven wrapped onto two lines and broke every
  // word mid-syllable on desktop. All eleven remain reachable at
  // /category/<slug>; this only limits what is shown.
  const NAV_ORDER = ["national", "politics", "business", "technology", "world"];
  const categories = NAV_ORDER.map((slug) => allCategories.find((c) => c.slug === slug))
    .filter((c): c is NonNullable<typeof c> => Boolean(c))
    .map((c) => ({ slug: c.slug, name_en: c.name_en, name_ne: c.name_ne }));

  return (
    <html lang="ne" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint, so a dark-mode
            reader never sees a white flash. */}
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="flex min-h-screen flex-col bg-white text-gray-900 antialiased font-body">
        <Header categories={categories} />
        <main className="flex-1">{children}</main>
        <Footer />
      </body>
    </html>
  );
}