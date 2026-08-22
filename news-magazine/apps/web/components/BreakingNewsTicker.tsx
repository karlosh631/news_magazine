"use client";
import Link from "next/link";
import type { Article } from "@/types/database";

export function BreakingNewsTicker({ items }: { items: Pick<Article, "id" | "slug" | "headline">[] }) {
  return (
    <div className="bg-red-700 text-white overflow-hidden" role="marquee" aria-label="Breaking news">
      <div className="max-w-6xl mx-auto flex items-center gap-4 px-4 py-2">
        <span className="shrink-0 font-bold uppercase text-xs tracking-wide bg-white text-red-700 px-2 py-1 rounded">
          Breaking
        </span>
        <div className="flex gap-8 overflow-x-auto no-scrollbar">
          {items.map((item) => (
            <Link key={item.id} href={`/article/${item.slug}`} className="whitespace-nowrap hover:underline text-sm">
              {item.headline}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
