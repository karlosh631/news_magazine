"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SafeImage } from "@/components/SafeImage";

type LiveItem = {
  id: string;
  title: string;
  summary: string;
  category: string;
  timestamp: string;
  imageUrl: string | null;
  videoUrl: string | null;
  audioUrl: string | null;
  source: string;
  url: string | null;
};

const labels = {
  en: { loading: "Loading...", empty: "Loading...", source: "Source", read: "Read Full Article" },
  ne: { loading: "लोड हुँदैछ...", empty: "लोड हुँदैछ...", source: "स्रोत", read: "पूरा समाचार पढ्नुहोस्" },
};

export function LiveNewsFeed({ category = "all", language = "en" }: { category?: string; language?: "en" | "ne" }) {
  const [items, setItems] = useState<LiveItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedLanguage, setSelectedLanguage] = useState<"en" | "ne">(language);
  const copy = labels[selectedLanguage];

  useEffect(() => {
    const stored = localStorage.getItem("site-language");
    if (stored === "en" || stored === "ne") setSelectedLanguage(stored);
    const onLanguageChange = (event: Event) => {
      const next = (event as CustomEvent<"en" | "ne">).detail;
      if (next === "en" || next === "ne") setSelectedLanguage(next);
    };
    window.addEventListener("site-language-change", onLanguageChange);
    return () => window.removeEventListener("site-language-change", onLanguageChange);
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch(`/api/live-news?category=${encodeURIComponent(category)}&language=${selectedLanguage}`, { cache: "no-store" });
        const nextItems = (await response.json()) as LiveItem[];
        if (active) setItems(Array.isArray(nextItems) ? nextItems : []);
      } catch {
        if (active) setItems([]);
      } finally {
        if (active) setLoading(false);
      }
    };

    load();
    const timer = window.setInterval(load, 3_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [category, selectedLanguage]);

  if (loading) return <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-72 animate-pulse rounded-lg bg-gray-100" />)}</div>;
  if (items.length === 0) return <div className="flex items-center justify-center gap-3 py-20 text-center text-gray-500"><span className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />{copy.empty}</div>;

  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item) => (
        <article key={item.id} className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="aspect-video bg-gray-100">
            <SafeImage src={item.imageUrl || ""} alt={item.title} className="h-full w-full object-cover" />
          </div>
          <div className="space-y-3 p-4">
            <span className="text-xs font-semibold uppercase text-gray-500">{item.category}</span>
            <h2 className="font-semibold leading-snug text-gray-900"><Link href={`/article/${item.id}`} className="hover:underline">{item.title}</Link></h2>
            {item.summary && <p className="line-clamp-3 text-sm text-gray-600">{item.summary}</p>}
            {item.videoUrl && <iframe src={item.videoUrl} title={item.title} className="aspect-video w-full" allowFullScreen />}
            {item.audioUrl && <audio controls src={item.audioUrl} className="w-full" />}
            <div className="flex items-center justify-between text-xs text-gray-500">
              <span>{item.source}</span>
              <Link href={`/article/${item.id}`} className="underline">{copy.read}</Link>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
