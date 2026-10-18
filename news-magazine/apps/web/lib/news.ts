/**
 * News data access.
 *
 * Every read the public site performs goes through here, so there is one
 * place that knows the SQL contract introduced by migration 0007.
 *
 * Stage 1 had no such layer: the homepage and category pages called
 * Gemini and rendered whatever came back, and only /search/page.tsx
 * touched the database. That is why the entire ingestion pipeline was
 * invisible to users — the data was being collected and then never read.
 */

import "server-only";

import { createPublicSupabaseClient } from "@/lib/supabase/server";
import type { LatestArticle, SearchResult } from "@/types/database";

export const PAGE_SIZE = 24;

/**
 * True when Supabase is not configured.
 *
 * The supabase client falls back to "placeholder.supabase.co" and
 * "missing-anon-key" rather than throwing, so every query "succeeds"
 * with zero rows. That is how the site rendered an empty homepage for
 * days while /api/health still reported the database as fine. Detecting
 * it here lets pages explain themselves instead of showing nothing.
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_URL !== "https://placeholder.supabase.co" &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== "missing-anon-key"
  );
}

export interface FeedResult {
  articles: LatestArticle[];
  error: string | null;
  configured: boolean;
}

/**
 * Newest-first, deduplicated articles for the homepage and category pages.
 *
 * Uses the latest_articles() RPC rather than a raw select so the
 * `duplicate_of is null` filter and the is_breaking ordering live in one
 * place in SQL instead of being re-derived in every component.
 */
export async function getLatestArticles(options?: {
  limit?: number;
  categorySlug?: string;
  excludeIds?: string[];
  language?: "ne" | "en";
}): Promise<FeedResult> {
  const { limit = PAGE_SIZE, categorySlug, excludeIds, language } = options ?? {};

  if (!isSupabaseConfigured()) {
    return {
      articles: [],
      error: "Supabase is not configured. Add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to apps/web/.env.local.",
      configured: false,
    };
  }

  // Normalise: an unknown or absent value means "show everything".
  const languageFilter =
    language === "ne" || language === "en" ? language : undefined;

  const supabase = createPublicSupabaseClient();

  let categoryId: string | null = null;
  if (categorySlug) {
    const category = await supabase
      .from("categories")
      .select("id")
      .eq("slug", categorySlug)
      .maybeSingle();

    if (category.error) {
      return { articles: [], error: category.error.message, configured: true };
    }
    // An unknown category slug is an empty feed, not an error.
    if (!category.data) {
      return { articles: [], error: null, configured: true };
    }
    categoryId = category.data.id;
  }

  const { data, error } = await supabase.rpc("latest_articles", {
    p_limit: limit,
    p_category: categoryId,
    p_exclude_ids: excludeIds ?? null,
  });

  if (error) {
    // The RPC needs migration 0007. Fall back to a plain select so the
    // site still works against an un-migrated database.
    console.error("[news] latest_articles failed, falling back:", error.message);
    return languageFilter
      ? languageSelect({ language: languageFilter, limit, categoryId })
      : fallbackSelect({ limit, categoryId });
  }

  if (languageFilter) {
    // The RPC does not return `language`, so a filtered read goes
    // straight to PostgREST where the filter can run in the database.
    return languageSelect({ language: languageFilter, limit, categoryId });
  }

  return { articles: (data ?? []) as LatestArticle[], error: null, configured: true };
}

/**
 * Read one language's coverage directly from the articles table.
 *
 * The latest_articles() RPC does not project `language`, so filtering by
 * it through PostgREST is the only way to make the toggle real. Both
 * Nepali and English editions of most outlets are ingested, so this
 * returns genuinely different stories rather than a translation.
 */
async function languageSelect({
  language,
  limit,
  categoryId,
}: {
  language: "ne" | "en";
  limit: number;
  categoryId: string | null;
}): Promise<FeedResult> {
  const supabase = createPublicSupabaseClient();

  const build = () => {
    let query = supabase
      .from("articles")
      .select(
        "id, slug, headline, excerpt, summary_points, language, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, published_at, source_name_snapshot, source_article_url, source_count"
      )
      .eq("status", "published")
      .is("duplicate_of", null)
      .eq("language", language)
      .order("published_at", { ascending: false, nullsFirst: false })
      .limit(limit);
    if (categoryId) query = query.eq("primary_category_id", categoryId);
    return query;
  };

  const { data, error } = await build();
  if (error) {
    return { articles: [], error: error.message, configured: true };
  }

  const rows = ((data ?? []) as unknown[]).map(mapArticleRow);

  // Too thin on its own? Top up from the other language rather than
  // showing a nearly empty page.
  if (rows.length < limit / 2) {
    const other = language === "en" ? "ne" : "en";
    const { data: fallbackData } = await build()
      .eq("language", other)
      .order("published_at", { ascending: false, nullsFirst: false })
      .limit(limit);

    const seen = new Set(rows.map((row) => row.id));
    for (const row of (fallbackData ?? []) as unknown[]) {
      const mapped = mapArticleRow(row);
      if (seen.has(mapped.id)) continue;
      seen.add(mapped.id);
      rows.push(mapped);
    }
  }

  return { articles: rows.slice(0, limit), error: null, configured: true };
}

/** Map a raw `articles` row to the card shape. */
function mapArticleRow(row: unknown): LatestArticle {
  const item = row as Record<string, unknown>;
  return {
    id: String(item.id),
    slug: String(item.slug),
    headline: String(item.headline),
    excerpt: (item.excerpt as string) ?? null,
    summary_points: (item.summary_points as string[]) ?? null,
    language: (item.language as "ne" | "en") ?? "ne",
    featured_image_url: (item.featured_image_url as string) ?? null,
    image_highres_url: (item.image_highres_url as string) ?? null,
    video_url: (item.video_url as string) ?? null,
    video_embed_url: (item.video_embed_url as string) ?? null,
    audio_url: (item.audio_url as string) ?? null,
    gif_url: (item.gif_url as string) ?? null,
    published_at: (item.published_at as string) ?? null,
    source_name_snapshot: (item.source_name_snapshot as string) ?? null,
    source_article_url: (item.source_article_url as string) ?? null,
    source_count: Number(item.source_count ?? 1),
    category_slug: null,
    category_name: null,
  };
}


async function fallbackSelect({
  limit,
  categoryId,
}: {
  limit: number;
  categoryId: string | null;
}): Promise<FeedResult> {
  const supabase = createPublicSupabaseClient();

  let query = supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, published_at, source_name_snapshot, source_article_url, source_count, primary_category_id"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    .order("published_at", { ascending: false })
    .limit(limit);

  if (categoryId) query = query.eq("primary_category_id", categoryId);

  const { data, error } = await query;
  if (error) {
    return { articles: [], error: error.message, configured: true };
  }

  return {
    articles: ((data ?? []) as unknown[]).map((row) => {
      const item = row as Record<string, unknown>;
      return {
        id: String(item.id),
        slug: String(item.slug),
        headline: String(item.headline),
        excerpt: (item.excerpt as string) ?? null,
        summary_points: (item.summary_points as string[]) ?? null,
        featured_image_url: (item.featured_image_url as string) ?? null,
        image_highres_url: (item.image_highres_url as string) ?? null,
        video_url: (item.video_url as string) ?? null,
        video_embed_url: (item.video_embed_url as string) ?? null,
        audio_url: (item.audio_url as string) ?? null,
        gif_url: (item.gif_url as string) ?? null,
        published_at: (item.published_at as string) ?? null,
        source_name_snapshot: (item.source_name_snapshot as string) ?? null,
        source_article_url: (item.source_article_url as string) ?? null,
        source_count: Number(item.source_count ?? 1),
        language: (item.language as "ne" | "en") ?? "ne",
        category_slug: null,
        category_name: null,
      } satisfies LatestArticle;
    }),
    error: null,
    configured: true,
  };
}

export interface SearchFeedResult {
  articles: SearchResult[];
  error: string | null;
  configured: boolean;
}

/**
 * Adapt a search row to the shape NewsCard renders.
 *
 * The two SQL functions return overlapping but differently-shaped rows
 * (search_articles has `rank`/`resurrected`, latest_articles has
 * `category_*`). NewsCard needs the card fields, so map explicitly
 * rather than casting — a cast would hide a genuine mismatch the next
 * time either function's signature changes.
 */
export function toCardArticle(
  row: SearchResult | LatestArticle
): LatestArticle {
  return {
    id: row.id,
    slug: row.slug,
    headline: row.headline,
    excerpt: row.excerpt ?? null,
    summary_points: row.summary_points ?? null,
    featured_image_url: row.featured_image_url ?? null,
    image_highres_url: row.image_highres_url ?? null,
    video_url: row.video_url ?? null,
    video_embed_url: row.video_embed_url ?? null,
    audio_url: row.audio_url ?? null,
    gif_url: row.gif_url ?? null,
    published_at: row.published_at ?? null,
    source_name_snapshot: row.source_name_snapshot ?? null,
    source_article_url: row.source_article_url ?? null,
    source_count: "source_count" in row ? Number(row.source_count ?? 1) : 1,
    category_slug: "category_slug" in row ? (row.category_slug ?? null) : null,
    category_name: "category_name" in row ? (row.category_name ?? null) : null,
  };
}

/** Database-only search. Never scrapes, so it is safe for every keystroke. */
export async function searchArticles(
  query: string,
  limit = PAGE_SIZE
): Promise<SearchFeedResult> {
  if (!isSupabaseConfigured()) {
    return {
      articles: [],
      error: "Supabase is not configured.",
      configured: false,
    };
  }

  const supabase = createPublicSupabaseClient();

  const { data, error } = await supabase.rpc("search_articles", {
    p_query: query,
    p_limit: limit,
    p_category: null,
    // Include pruned rows: the search page calls the Python service to
    // resurrect anything older than the retention window.
    p_include_pruned: true,
  });

  if (error) {
    console.error("[news] search_articles failed:", error.message);
    return { articles: [], error: error.message, configured: true };
  }

  return { articles: (data ?? []) as SearchResult[], error: null, configured: true };
}

export async function getArticleBySlug(slug: string) {
  if (!isSupabaseConfigured()) return null;

  const supabase = createPublicSupabaseClient();

  const { data, error } = await supabase
    .from("articles")
    .select("*")
    .eq("slug", slug)
    .eq("status", "published")
    .is("duplicate_of", null)
    .maybeSingle();

  if (error) {
    console.error("[news] article lookup failed:", error.message);
    return null;
  }

  return data;
}

export async function getRelatedArticles(
  categoryId: string | null,
  excludeId: string,
  limit = 6
): Promise<LatestArticle[]> {
  if (!isSupabaseConfigured() || !categoryId) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, published_at, source_name_snapshot, source_article_url, source_count"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    .eq("primary_category_id", categoryId)
    .neq("id", excludeId)
    .order("published_at", { ascending: false })
    .limit(limit);

  if (error) {
    console.error("[news] related lookup failed:", error.message);
    return [];
  }

  return ((data ?? []) as unknown[]).map((row) => {
    const item = row as Record<string, unknown>;
    return {
      id: String(item.id),
      slug: String(item.slug),
      headline: String(item.headline),
      excerpt: (item.excerpt as string) ?? null,
      summary_points: (item.summary_points as string[]) ?? null,
      featured_image_url: (item.featured_image_url as string) ?? null,
      image_highres_url: (item.image_highres_url as string) ?? null,
      video_url: (item.video_url as string) ?? null,
      video_embed_url: (item.video_embed_url as string) ?? null,
      audio_url: (item.audio_url as string) ?? null,
      gif_url: (item.gif_url as string) ?? null,
      published_at: (item.published_at as string) ?? null,
      source_name_snapshot: (item.source_name_snapshot as string) ?? null,
      source_article_url: (item.source_article_url as string) ?? null,
      source_count: Number(item.source_count ?? 1),
      category_slug: null,
      category_name: null,
    } satisfies LatestArticle;
  });
}

export async function getCategories() {
  if (!isSupabaseConfigured()) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("categories")
    .select("id, slug, name_en, name_ne, sort_order")
    .order("sort_order", { ascending: true });

  if (error) {
    console.error("[news] categories lookup failed:", error.message);
    return [];
  }

  return data ?? [];
}

/** Breaking news, for the ticker. */
export async function getBreakingNews(limit = 10): Promise<LatestArticle[]> {
  if (!isSupabaseConfigured()) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, published_at, source_name_snapshot, source_article_url, source_count"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    .eq("is_breaking", true)
    .order("published_at", { ascending: false })
    .limit(limit);

  if (error) {
    console.error("[news] breaking lookup failed:", error.message);
    return [];
  }

  return ((data ?? []) as unknown[]).map((row) => {
    const item = row as Record<string, unknown>;
    return {
      id: String(item.id),
      slug: String(item.slug),
      headline: String(item.headline),
      excerpt: (item.excerpt as string) ?? null,
      summary_points: (item.summary_points as string[]) ?? null,
      featured_image_url: (item.featured_image_url as string) ?? null,
      image_highres_url: (item.image_highres_url as string) ?? null,
      video_url: (item.video_url as string) ?? null,
      video_embed_url: (item.video_embed_url as string) ?? null,
      audio_url: (item.audio_url as string) ?? null,
      gif_url: (item.gif_url as string) ?? null,
      published_at: (item.published_at as string) ?? null,
      source_name_snapshot: (item.source_name_snapshot as string) ?? null,
      source_article_url: (item.source_article_url as string) ?? null,
      source_count: Number(item.source_count ?? 1),
      category_slug: null,
      category_name: null,
    } satisfies LatestArticle;
  });
}