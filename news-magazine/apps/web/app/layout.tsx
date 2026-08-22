import type { Metadata } from "next";
import "./globals.css";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";

export const metadata: Metadata = {
  title: { default: "Nepal News & Magazine", template: "%s | Nepal News & Magazine" },
  description: "Latest Nepali and English news, opinion, and features.",
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://example.com"),
  openGraph: { type: "website", locale: "ne_NP" },
  manifest: "/manifest.json",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ne">
      <body className="min-h-screen bg-white text-gray-900 antialiased font-body">
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
