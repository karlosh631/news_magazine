import type { MetadataRoute } from "next";

import { createPublicSupabaseClient } from "@/lib/supabase/server";

export const revalidate = 300;

const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://new24hr.vercel.app";

/**
 * Sitemap.
 *
 * Only published, non-duplicate articles are listed — a URL that 404s
 * or resolves to a merged copy is worse than no URL at all in a sitemap.
 * Slugs are stable, so this does not need rebuilding on every deploy.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();

  const entries: MetadataRoute.Sitemap = [
    { url: SITE_URL, lastModified: now, changeFrequency: "hourly", priority: 1 },
    {
      url: `${SITE_URL}/search`,
      lastModified: now,
      changeFrequency: "daily",
      priority: 0.3,
    },
  ];

  try {
    const supabase = createPublicSupabaseClient();

    const { data: categories } = await supabase
      .from("categories")
      .select("slug")
      .order("sort_order", { ascending: true });

    for (const category of categories ?? []) {
      entries.push({
        url: `${SITE_URL}/category/${category.slug}`,
        lastModified: now,
        changeFrequency: "hourly",
        priority: 0.6,
      });
    }

    const { data: articles } = await supabase
      .from("articles")
      .select("slug, updated_at, published_at")
      .eq("status", "published")
      .is("duplicate_of", null)
      .order("published_at", { ascending: false })
      .limit(2000);

    for (const article of articles ?? []) {
      const last = article.updated_at ?? article.published_at ?? now;
      entries.push({
        url: `${SITE_URL}/article/${article.slug}`,
        lastModified: new Date(last),
        changeFrequency: "weekly",
        priority: 0.8,
      });
    }
  } catch {
    // A sitemap that omits entries still beats a 500 that crawlers
    // treat as a hard failure.
  }

  return entries;
}