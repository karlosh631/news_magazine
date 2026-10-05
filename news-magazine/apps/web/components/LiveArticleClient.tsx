"use client";

import { useEffect, useState } from "react";
import { SafeImage } from "@/components/SafeImage";

type LiveItem = {
  id: string;
  title: string;
  summary: string;
  timestamp: string;
  imageUrl: string | null;
  videoUrl: string | null;
  audioUrl: string | null;
  source: string;
  url: string | null;
};

export function LiveArticleClient({ id }: { id: string }) {
  const [item, setItem] = useState<LiveItem | null>(null);

  useEffect(() => {
    fetch("/api/live-news?category=all", { cache: "no-store" })
      .then((response) => response.json())
      .then((items: LiveItem[]) => setItem(items.find((candidate) => candidate.id === id) ?? null))
      .catch(() => setItem(null));
  }, [id]);

  if (!item) return <main className="mx-auto max-w-4xl px-4 py-24 text-center">Loading...</main>;

  return (
    <main className="mx-auto max-w-4xl px-4 py-10">
      <article>
        <h1 className="font-headline text-3xl font-bold leading-tight md:text-5xl">{item.title}</h1>
        <time className="mt-4 block text-sm text-gray-500">{new Date(item.timestamp).toLocaleString()}</time>
        <div className="relative mt-8 aspect-video overflow-hidden rounded-xl bg-gray-100">
          <SafeImage src={item.imageUrl || ""} alt={item.title} className="h-full w-full object-cover" priority />
        </div>
        {item.videoUrl && <iframe src={item.videoUrl} title={item.title} className="mt-8 aspect-video w-full rounded-xl" allowFullScreen />}
        {item.audioUrl && <audio src={item.audioUrl} controls className="mt-8 w-full" />}
        <p className="mt-8 text-lg leading-8 text-gray-800">{item.summary}</p>
        {item.url && <p className="mt-8 rounded-lg border bg-gray-50 p-5 text-sm"><span>Source / Reference: </span><a href={item.url} target="_blank" rel="noreferrer" className="font-semibold underline">{item.source}</a></p>}
      </article>
    </main>
  );
}
