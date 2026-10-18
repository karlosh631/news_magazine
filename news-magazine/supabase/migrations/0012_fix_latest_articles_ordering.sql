-- =========================================================
-- 0012_fix_latest_articles_ordering.sql
-- Makes "latest" actually mean latest.
--
-- The bug
-- -------
-- latest_articles() ordered by:
--
--     order by a.is_breaking desc, a.published_at desc nulls last, ...
--
-- is_breaking is the PRIMARY sort key. That means every article flagged
-- breaking outranks every article that is not, no matter how old it is.
-- Once enough breaking stories exist to fill the homepage's 24 slots,
-- they occupy all of them and genuinely newer articles are pushed off
-- the end of the list.
--
-- What that looked like in production: the homepage's newest card stayed
-- pinned at 8:48 PM for over an hour while the database held articles
-- from 10:07 PM. Comparing the two code paths made it obvious, because
-- they call the same function:
--
--     /api/live-news?limit=8    -> newest 16:22 UTC
--     /api/live-news?limit=24   -> newest 15:03 UTC
--
-- A strict "published_at desc" cannot return newer rows for a SMALLER
-- limit. The only explanation is a sort key that reorders the set.
--
-- The fix
-- -------
-- published_at leads; is_breaking only breaks ties between articles
-- published at the same instant. Breaking news keeps its own emphasis
-- where it belongs — BreakingNewsTicker calls getBreakingNews(), which
-- filters is_breaking = true and orders by published_at desc. Nothing is
-- lost; the homepage just stops being held hostage by the flag.
--
-- Only the ORDER BY changes. The signature and every projected column
-- are identical to 0007, so no caller changes.
--
-- Ordering of this file: 0012 replaces the 0007 definition because
-- CREATE OR REPLACE on the same signature takes the later body.
-- =========================================================

create or replace function latest_articles(
  p_limit integer default 24,
  p_category uuid default null,
  p_exclude_ids uuid[] default null
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
  published_at timestamptz,
  source_name_snapshot text,
  source_article_url text,
  source_count integer,
  category_slug text,
  category_name text
)
language sql
stable
security definer
set search_path = public
as $$
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
    a.published_at,
    a.source_name_snapshot,
    a.source_article_url,
    a.source_count,
    c.slug,
    coalesce(c.name_ne, c.name_en)
  from articles a
  left join categories c on c.id = a.primary_category_id
  where a.status = 'published'
    and a.duplicate_of is null
    and (p_category is null or a.primary_category_id = p_category)
    and (
      p_exclude_ids is null
      or cardinality(p_exclude_ids) = 0
      or not (a.id = any(p_exclude_ids))
    )
  order by
    a.published_at desc nulls last,
    a.is_breaking desc,
    a.ingested_at desc,
    a.id desc
  limit greatest(coalesce(p_limit, 24), 1);
$$;

comment on function latest_articles(integer, uuid, uuid[]) is
  'Newest published, non-duplicate articles. Ordered by published_at first; '
  'is_breaking only breaks ties, because as the primary key it pinned old '
  'breaking stories above newer news on the homepage.';

grant execute on function latest_articles(integer, uuid, uuid[]) to service_role, anon, authenticated;

-- ---------------------------------------------------------
-- Verify after applying
--
--   -- These must now agree, and both must show the newest row:
--   select published_at from latest_articles(8);
--   select published_at from latest_articles(24);
--
--   -- Should match the table's true maximum:
--   select max(published_at) from articles
--    where status = 'published' and duplicate_of is null;
--
-- If they still differ, another function definition is shadowing this
-- one (check pg_proc for a second latest_articles entry).
-- ---------------------------------------------------------