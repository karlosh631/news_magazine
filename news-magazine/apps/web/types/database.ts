/**
 * Database types.
 *
 * Stage 1's hand-authored `Database` type declared `Tables` as a flat
 * object keyed by table name. @supabase/supabase-js v2 expects
 *
 *   Database = { public: { Tables: { <name>: { Row; Insert; Update } } } }
 *
 * i.e. each table carries its own Row/Insert/Update triple. The old shape
 * is structurally wrong, so every `.from("articles").select()` inferred
 * `any` and TypeScript never caught a single bad column name — which is
 * how `item.abstract`, `item.title`, `item.cover_image_url` and
 * `item.category` (none of which exist on `articles`) shipped to
 * production undetected.
 *
 * Once the project is linked, regenerate with:
 *
 *   npx supabase gen types typescript --project-id <project-id> > types/database.ts
 *
 * and keep the JSDoc note above.
 */

export type ArticleStatus =
  | "ingested"
  | "pending_review"
  | "editor_review"
  | "approved"
  | "published"
  | "scheduled"
  | "archived"
  | "rejected";

/**
 * One row of `articles`.
 *
 * Columns added by 0004 (media) and 0007 (ingestion hardening). Every
 * field here exists in the database; the list is deliberately explicit
 * rather than an index signature so a typo is a compile error.
 */
export interface ArticleRow {
  id: string;
  slug: string;
  headline: string;
  subtitle: string | null;
  language: string;
  status: ArticleStatus;
  is_breaking: boolean;
  is_featured: boolean;

  body_html: string | null;
  excerpt: string | null;

  /** Exactly four points, verbatim from the source article. Never generated. */
  summary_points: string[] | null;
  summary_method: string | null;

  /**
   * Machine translation, written by apps/ingestion/scripts/
   * translate_content.py (migration 0011). The originals above are
   * never overwritten.
   */
  headline_translated: string | null;
  summary_points_translated: string[] | null;
  translation_language: string | null;
  translated_by: string | null;
  translated_at: string | null;

  featured_image_url: string | null;
  featured_image_alt: string | null;
  image_highres_url: string | null;
  video_url: string | null;
  video_embed_url: string | null;
  audio_url: string | null;
  gif_url: string | null;

  /**
   * When this story's picture was confirmed against the story's OWN
   * page.
   *
   * Null means it was never confirmed: the picture came from the
   * publisher's feed, or from a scan of the page, and nobody has proved
   * it belongs to this story. The card does not render an unconfirmed
   * picture — see the operator's rule in components/NewsCard.tsx.
   */
  image_verified_at: string | null;

  source_id: string | null;
  source_article_url: string | null;
  source_name_snapshot: string | null;

  primary_category_id: string | null;

  canonical_url: string | null;
  title_normalized: string | null;
  content_hash: string | null;

  duplicate_of: string | null;
  source_count: number;

  published_at: string | null;
  scheduled_for: string | null;
  ingested_at: string | null;
  last_seen_at: string | null;
  resurrected_at: string | null;
  scrape_query: string | null;
  word_count: number | null;

  view_count: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryRow {
  id: string;
  slug: string;
  name_en: string;
  name_ne: string | null;
  parent_id: string | null;
  sort_order: number;
}

export interface SourceRow {
  id: string;
  name: string;
  base_url: string;
  feed_url: string | null;
  fallback_url: string | null;
  site_url: string | null;
  adapter_key: string;
  enabled: boolean;
  allowed: boolean;
  sector: string | null;
  poll_interval_seconds: number;
  rate_limit_per_minute: number;
  last_success_at: string | null;
  consecutive_failures: number;
  notes: string | null;
}

/** Shape returned by the latest_articles() SQL function. */
export interface LatestArticle {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  /** The language the outlet published this story in. */
  language?: "ne" | "en";
  summary_points: string[] | null;
  featured_image_url: string | null;
  image_highres_url: string | null;
  video_url: string | null;
  video_embed_url: string | null;
  audio_url: string | null;
  gif_url: string | null;

  /**
   * When this story's picture was confirmed against the story's OWN
   * page.
   *
   * Null means it was never confirmed: the picture came from the
   * publisher's feed, or from a scan of the page, and nobody has proved
   * it belongs to this story. The card does not render an unconfirmed
   * picture — see the operator's rule in components/NewsCard.tsx.
   */
  image_verified_at: string | null;
  published_at: string | null;
  /**
   * When WE first saw this story — not when the outlet published it.
   *
   * The two differ, and the difference is what the reader feels. A
   * story published an hour ago whose publisher only just put it in
   * their feed is NEW to this site, and it should appear as new. A
   * story published two minutes ago that we fetched ten minutes ago
   * has been on the homepage the whole time, and it should not keep
   * jumping back to the top every time the feed polls.
   *
   * Sorting by this is therefore what "newest on top" means in
   * practice: a story moves up once, when it arrives, and then stays
   * where it is.
   */
  ingested_at: string | null;
  source_name_snapshot: string | null;
  source_article_url: string | null;
  source_count: number;
  category_slug: string | null;
  category_name: string | null;
}

/** Shape returned by the search_articles() SQL function. */
export interface SearchResult {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  summary_points: string[] | null;
  featured_image_url: string | null;
  image_highres_url: string | null;
  video_url: string | null;
  video_embed_url: string | null;
  audio_url: string | null;
  gif_url: string | null;

  /**
   * When this story's picture was confirmed against the story's OWN
   * page.
   *
   * Null means it was never confirmed: the picture came from the
   * publisher's feed, or from a scan of the page, and nobody has proved
   * it belongs to this story. The card does not render an unconfirmed
   * picture — see the operator's rule in components/NewsCard.tsx.
   */
  image_verified_at: string | null;
  published_at: string | null;
  source_name_snapshot: string | null;
  source_article_url: string | null;
  rank: number;
  resurrected: boolean;
}

type Table<T> = {
  Row: T;
  Insert: Partial<T>;
  Update: Partial<T>;
  Relationships: [];
};

export interface Database {
  public: {
    Tables: {
      articles: Table<ArticleRow>;
      categories: Table<CategoryRow>;
      sources: Table<SourceRow>;
    };
    Views: Record<string, never>;
    Functions: {
      latest_articles: {
        Args: {
          p_limit?: number;
          p_category?: string | null;
          p_exclude_ids?: string[] | null;
        };
        Returns: LatestArticle[];
      };
      search_articles: {
        Args: {
          p_query: string;
          p_limit?: number;
          p_category?: string | null;
          p_include_pruned?: boolean;
        };
        Returns: SearchResult[];
      };
    };
    Enums: {
      article_status: ArticleStatus;
    };
    CompositeTypes: Record<string, never>;
  };
}