-- =============================================================
-- 0027_search_articles_image_verified_at.sql
-- Let the search path tell a confirmed picture from an unconfirmed one.
--
-- WHY THIS EXISTS
-- ---------------
-- `articles.image_verified_at` (migration 0016) records whether the
-- picture was confirmed against the story's OWN page. The homepage and
-- the category pages read the column directly; `search_articles()`
-- did not return it, so the search page had no way to know and a
-- search result could show a photograph that belonged to a different
-- story.
--
-- Replacing the function is the only way to add a column to its
-- result set. The body is otherwise identical to migration 0007.
--
-- Idempotent: `create or replace function` only.
-- =============================================================

create or replace function search_articles(
  p_query text,
  p_limit integer default 24,
  p_category uuid default null,
  p_include_pruned boolean default false
)
returns table (
  id uuid,
  slug text,
  headline text,
  excerpt text,
  summary_points jsonb,
  featured_image_url text,
  image_highres_url text,
  video_url text,
  video_embed_url text,
  audio_url text,
  gif_url text,
  image_verified_at timestamptz,
  published_at timestamptz,
  source_name_snapshot text,
  source_article_url text,
  source_count integer,
  rank real,
  resurrected boolean
)
language sql
stable
security definer
set search_path = public
as $$
  with q as (
    select
      trim(p_query) as raw,
      -- websearch_to_tsquery never raises on user input the way
      -- to_tsquery does on malformed syntax.
      websearch_to_tsquery('simple', coalesce(p_query, '')) as tsq
  )
  select
    a.id,
    a.slug,
    a.headline,
    a.excerpt,
    a.summary_points,
    a.featured_image_url,
    a.image_highres_url,
    a.video_url,
    a.video_embed_url,
    a.audio_url,
    a.gif_url,
    a.image_verified_at,
    a.published_at,
    a.source_name_snapshot,
    a.source_article_url,
    a.source_count,
    greatest(
      ts_rank_cd(a.search_vector, q.tsq),
      similarity(a.title_normalized, lower(q.raw)) * 0.9
    ) as rank,
    (a.resurrected_at is not null) as resurrected
  from articles a
  cross join q
  where a.status = 'published'
    and a.duplicate_of is null
    and (
      p_include_pruned = true
      or coalesce(a.published_at, a.ingested_at, a.created_at)
         >= now() - interval '48 hours'
    )
    and (p_category is null or a.primary_category_id = p_category)
    and (
      q.tsq is null
      or a.search_vector @@ q.tsq
      or a.title_normalized is not null
         and similarity(a.title_normalized, lower(q.raw)) >= 0.3
    )
  order by rank desc, a.published_at desc nulls last
  limit greatest(coalesce(p_limit, 24), 1);
$$;

comment on function search_articles is
  'Full-text + trigram search over published, non-duplicate articles. Falls back to trigram similarity so Nepali headlines still match. Carries image_verified_at so the read path can tell a confirmed picture from an unconfirmed one.';

-- The cleanup and repair queries both filter on a picture being present
-- and unverified. The table is capped at 10 000 rows, but the partial
-- index makes the sweep free rather than a scan.
create index if not exists articles_unverified_image_idx
  on articles (ingested_at)
  where image_verified_at is null and featured_image_url is not null;
