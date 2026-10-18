-- =========================================================
-- 0007_ingestion_hardening.sql
-- Production hardening for the autonomous ingestion pipeline.
--
-- Adds what the 20+ portal ingestion service needs but Stage 1 never
-- had: fuzzy dedup support, TTL pruning, on-demand "resurrection"
-- of pruned stories, HD media columns, and search that actually
-- works for Devanagari content.
--
-- Safe to apply to an existing production database (every statement
-- is idempotent).
-- =========================================================

-- ---------------------------------------------------------
-- SOURCES: per-outlet scraping configuration
--
-- Stage 1's `sources` table only described an RSS feed. The ingestion
-- service needs per-domain selectors, a separate HTML fallback URL,
-- and a sector label so operators can enable an outlet without a
-- code deploy.
-- ---------------------------------------------------------

alter table sources
  add column if not exists site_url text,                -- human homepage, used for attribution
  add column if not exists fallback_url text,            -- HTML listing page when the feed is thin
  add column if not exists selector_config jsonb,        -- per-domain CSS selectors for the HTML adapter
  add column if not exists sector text default 'national',
  add column if not exists user_agent text,              -- per-outlet User-Agent when required
  add column if not exists last_success_at timestamptz,
  add column if not exists consecutive_failures integer not null default 0;

comment on column sources.fallback_url is
  'HTML listing page scraped by the html_listing adapter when the RSS feed returns too few items.';
comment on column sources.selector_config is
  'JSON: {item, link, title, image, date, category} CSS selectors for the html_listing adapter.';

-- ---------------------------------------------------------
-- ARTICLES: dedup lineage, TTL bookkeeping, richer media
-- ---------------------------------------------------------

alter table articles
  add column if not exists duplicate_of uuid references articles(id) on delete set null,
  add column if not exists source_count integer not null default 1,
  add column if not exists last_seen_at timestamptz,
  add column if not exists resurrected_at timestamptz,
  add column if not exists resurrected_count integer not null default 0,
  add column if not exists image_highres_url text,
  add column if not exists video_embed_url text,
  add column if not exists audio_description text,
  add column if not exists word_count integer,
  add column if not exists scrape_query text,
  add column if not exists summary_points jsonb,
  add column if not exists summary_method text;

comment on column articles.duplicate_of is
  'Set when this row is a syndicated copy of another article. Rows with a non-null value are never shown.';
comment on column articles.source_count is
  'How many distinct outlets carried this story. A high count with one surviving row means dedup merged them.';
comment on column articles.resurrected_at is
  'Set when a pruned article was re-fetched on demand because a user searched for it.';
comment on column articles.scrape_query is
  'The search query that triggered on-demand resurrection, for auditability.';
comment on column articles.summary_points is
  'JSON array of exactly four points. Every entry is a verbatim sentence from the source article, never generated text.';

-- Guard the shape so a bad write cannot leave the card renderer reading
-- a string where it expects a list. Wrapped in a DO block so re-running
-- this migration on an already-patched database is a no-op rather than a
-- duplicate-constraint error.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'articles_summary_shape'
  ) then
    alter table articles
      add constraint articles_summary_shape
      check (
        summary_points is null
        or jsonb_typeof(summary_points) = 'array'
      ) not valid;
  end if;
end;
$$;

create index if not exists articles_published_desc_idx
  on articles (published_at desc nulls last)
  where status = 'published' and duplicate_of is null;

create index if not exists articles_last_seen_idx
  on articles (last_seen_at desc nulls last);

-- Fuzzy title lookup scoped to the 12-hour syndicated-copy window.
-- This is the index the ingestion worker's rapidfuzz pre-check runs
-- against: candidate rows only, exact match happens in Postgres.
create index if not exists articles_title_norm_idx
  on articles (title_normalized)
  where title_normalized is not null;

create index if not exists articles_title_trgm_pub_idx
  on articles using gin (title_normalized gin_trgm_ops)
  where status = 'published' and duplicate_of is null;

-- Near-duplicate candidate lookup used by the fuzzy dedup pre-check.
create index if not exists articles_dedup_window_idx
  on articles (ingested_at desc)
  where duplicate_of is null and status = 'published';

-- ---------------------------------------------------------
-- FUZZY DEDUPLICATION HELPER
--
-- Returns candidate rows published within the window whose headline
-- is within `threshold` trigram similarity of `needle`. The Python
-- side confirms with rapidfuzz and picks the cleanest version; this
-- only narrows the candidate set so we never pull thousands of rows
-- into memory every poll.
-- ---------------------------------------------------------

create or replace function find_duplicate_candidates(
  p_title_normalized text,
  p_since timestamptz,
  p_threshold real default 0.82
)
returns table (
  id uuid,
  headline text,
  canonical_url text,
  content_hash text,
  featured_image_url text,
  video_url text,
  excerpt text,
  published_at timestamptz,
  similarity real
)
language sql
stable
security definer
set search_path = public
as $$
  select
    a.id,
    a.headline,
    a.canonical_url,
    a.content_hash,
    a.featured_image_url,
    a.video_url,
    a.excerpt,
    a.published_at,
    similarity(a.title_normalized, p_title_normalized) as similarity
  from articles a
  where a.duplicate_of is null
    and a.status = 'published'
    and a.title_normalized is not null
    and a.ingested_at >= coalesce(p_since, now() - interval '12 hours')
    and similarity(a.title_normalized, p_title_normalized) >= p_threshold
  order by similarity desc
  limit 20;
$$;

comment on function find_duplicate_candidates is
  'Candidate syndicated copies of a headline within the dedup window. Narrowed by pg_trgm so the Python side only fuzzy-confirms a small set.';

-- ---------------------------------------------------------
-- TTL PRUNING
--
-- Requirement: keep the database light by removing articles older
-- than a retention window, but never touch rows a user is actively
-- reading (featured / breaking) and never touch resurrected rows
-- until their own TTL expires.
-- ---------------------------------------------------------

create or replace function prune_expired_articles(
  p_retention_hours integer default 48,
  p_max_rows integer default 10000
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_deleted integer := 0;
  v_by_ttl integer := 0;
begin
  -- 1. TTL pass: anything past the retention window, oldest first.
  with expired as (
    select a.id
    from articles a
    where a.status = 'published'
      and a.duplicate_of is null
      and not a.is_breaking
      and not a.is_featured
      and coalesce(a.published_at, a.ingested_at, a.created_at)
          < now() - make_interval(hours => p_retention_hours)
    order by coalesce(a.published_at, a.ingested_at, a.created_at) asc
    limit greatest(p_max_rows, 1000)
  )
  delete from articles a using expired
  where a.id = expired.id;
  get diagnostics v_by_ttl = row_count;
  v_deleted := v_deleted + v_by_ttl;

  -- 2. Row-cap pass: if the table is still over the cap, drop the
  --    oldest non-breaking rows regardless of age.
  if (select count(*) from articles) > p_max_rows then
    with over_cap as (
      select a.id
      from articles a
      where not a.is_breaking and not a.is_featured
      order by coalesce(a.published_at, a.ingested_at, a.created_at) asc
      limit (
        select greatest(
          (select count(*) from articles) - p_max_rows,
          0
        )
      )
    )
    delete from articles a using over_cap
    where a.id = over_cap.id;
    get diagnostics v_by_ttl = row_count;
    v_deleted := v_deleted + v_by_ttl;
  end if;

  return v_deleted;
end;
$$;

comment on function prune_expired_articles is
  'Deletes articles past their TTL (default 48h) then enforces the row cap. Breaking and featured rows are never pruned automatically.';

-- ---------------------------------------------------------
-- SEARCH
--
-- Stage 1's articles_search_vector is maintained by trigger but was
-- never queried by anything, and no route existed. This function is
-- what the search page and the /api/scrape-on-demand endpoint call.
--
-- 'simple' tsvector config is deliberate: a large share of content is
-- Devanagari and English stemming would silently degrade it. Trigram
-- similarity catches Nepali headlines that tsvector cannot stem.
-- ---------------------------------------------------------

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
  'Full-text + trigram search over published, non-duplicate articles. Falls back to trigram similarity so Nepali headlines still match.';

-- ---------------------------------------------------------
-- HOMEPAGE FEED
--
-- One query for the homepage / category pages: newest first,
-- deduplicated, with media resolved.
-- ---------------------------------------------------------

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
  order by a.is_breaking desc, a.published_at desc nulls last, a.ingested_at desc
  limit greatest(coalesce(p_limit, 24), 1);
$$;

-- ---------------------------------------------------------
-- RESURRECTION AUDIT LOG
--
-- Lets us answer "why did this old story come back?" and rate-limit
-- the on-demand scrape endpoint per query.
-- ---------------------------------------------------------

create table if not exists scrape_requests (
  id uuid primary key default uuid_generate_v4(),
  query text not null,
  requested_at timestamptz not null default now(),
  result_count integer not null default 0,
  resurrected_count integer not null default 0,
  duration_ms integer,
  caller text,
  ip_address text
);

create index if not exists scrape_requests_query_time_idx
  on scrape_requests (lower(query), requested_at desc);

comment on table scrape_requests is
  'Audit trail for on-demand scraping. Also the rate-limit source: the endpoint refuses a query polled more than N times per hour.';

-- ---------------------------------------------------------
-- GRANTS
--
-- These functions are security definer because the ingestion worker
-- and the public frontend both need to read rows that RLS would
-- otherwise filter by session. They return only published,
-- non-duplicate articles, so this widens nothing a reader could not
-- already see.
-- ---------------------------------------------------------

grant execute on function find_duplicate_candidates(text, timestamptz, real) to service_role;
grant execute on function prune_expired_articles(integer, integer) to service_role;
grant execute on function search_articles(text, integer, uuid, boolean) to service_role, anon, authenticated;
grant execute on function latest_articles(integer, uuid, uuid[]) to service_role, anon, authenticated;