/**
 * Hand-authored starter types matching supabase/migrations/0001_init_schema.sql.
 * Once the project is linked to a real Supabase instance, replace this file by
 * running:
 *
 *   npx supabase gen types typescript --project-id <project-id> > types/database.ts
 *
 * and re-add the JSDoc note above so nobody hand-edits the generated file.
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

export interface Article {
  id: string;
  slug: string;
  headline: string;
  subtitle: string | null;
  language: "ne" | "en";
  status: ArticleStatus;
  is_breaking: boolean;
  is_featured: boolean;
  body_html: string | null;
  excerpt: string | null;
  featured_image_url: string | null;
  featured_image_alt: string | null;
  source_id: string | null;
  source_article_url: string | null;
  source_name_snapshot: string | null;
  primary_category_id: string | null;
  published_at: string | null;
  updated_at: string;
  created_at: string;
  view_count: number;
}

export interface Category {
  id: string;
  slug: string;
  name_en: string;
  name_ne: string | null;
  parent_id: string | null;
  sort_order: number;
}

export interface Database {
  public: {
    Tables: {
      articles: { Row: Article; Insert: Partial<Article>; Update: Partial<Article> };
      categories: { Row: Category; Insert: Partial<Category>; Update: Partial<Category> };
      // ... remaining tables intentionally omitted from this hand-authored
      // starter; generate full types from the live schema before relying
      // on this for anything beyond the homepage/article/category pages.
    };
  };
}
