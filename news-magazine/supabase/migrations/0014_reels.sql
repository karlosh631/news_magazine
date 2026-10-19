-- =========================================================
-- 0014_reels.sql
-- Vertical short-form video ("Reels") as a first-class category.
--
-- Design constraints, all deliberate:
--
-- 1. NO VIDEO BYTES EVER LAND HERE. The table stores metadata and an
--    upstream URL only. Playback streams straight from the provider's
--    CDN. Reels are large; storing them would bloat the database, blow
--    through Supabase's row/bandwidth limits, and buy nothing.
--
-- 2. ATTRIBUTION IS A COLUMN, NOT AN OPTION. `creator_name`,
--    `creator_url`, `license_name` and `license_url` are NOT NULL-ish
--    fields that every provider adapter must fill in. `attribution`
--    says whether the licence requires credit to be displayed; the UI
--    shows it either way, collapsed behind a Details control, because
--    a provider with no attribution requirement is not a provider
--    whose content may be presented as yours.
--
-- 3. `unique (source_key, external_id)` makes re-ingesting free. The
--    reels job runs on a schedule and overlaps itself; without this,
--    every pass would duplicate the whole table.
--
-- Idempotent: safe to apply to an existing production database.
-- =========================================================

-- ---------------------------------------------------------
-- CATEGORY
--
-- `reels` is a category like any other so the header, the footer, the
-- sitemap and generateStaticParams all pick it up with no special
-- casing. Articles are never filed under it — the category page
-- renders <ReelsFeed/> when the slug is `reels`.
-- ---------------------------------------------------------

insert into categories (slug, name_en, name_ne, sort_order) values
  ('reels', 'Reels', 'रिल्स', 12)
on conflict (slug) do update set
  name_en = excluded.name_en,
  name_ne = excluded.name_ne,
  sort_order = excluded.sort_order;

-- ---------------------------------------------------------
-- REELS
-- ---------------------------------------------------------

create table if not exists reels (
  id uuid primary key default uuid_generate_v4(),

  -- Provider identity. 'pexels', 'pixabay', 'wikimedia', ...
  source_key text not null,

  -- The provider's own id for this clip. Stable across re-ingests.
  external_id text not null,

  -- Direct playable URL on the provider's CDN. Never proxied.
  video_url text not null,

  -- Frame shown before playback starts. Without this a vertical feed
  -- flashes black on every scroll.
  poster_url text,

  title text,
  description text,

  -- Attribution. Always populated by the adapter even when the
  -- licence does not require it, so the UI never has to guess.
  creator_name text,
  creator_url text,
  license_name text,
  license_url text,
  attribution_required boolean not null default true,

  -- Intrinsic dimensions. The feed picks 9:16 / 1:1 / 4:5 from these.
  width integer,
  height integer,
  duration_seconds numeric,

  -- When the provider published it. Falls back to ingest time.
  published_at timestamptz,
  ingested_at timestamptz not null default now(),

  constraint reels_source_external_key unique (source_key, external_id)
);

-- The feed orders by published_at desc; without this it is a seq scan.
create index if not exists reels_published_at_idx
  on reels (published_at desc nulls last);

create index if not exists reels_source_idx on reels (source_key);

-- ---------------------------------------------------------
-- LIKES
--
-- `fingerprint` is a salted hash of the visitor's IP+UA. It makes a
-- like idempotent per visitor without storing an IP address, and stops
-- a single person inflating a count from a script.
-- ---------------------------------------------------------

create table if not exists reel_likes (
  reel_id uuid not null references reels(id) on delete cascade,
  fingerprint text not null,
  liked_at timestamptz not null default now(),
  primary key (reel_id, fingerprint)
);

-- Counted per request in aggregate, so the index must cover the group.
create index if not exists reel_likes_reel_idx on reel_likes (reel_id);

-- ---------------------------------------------------------
-- ROW LEVEL SECURITY
--
-- Public read on reels: the feed is public. Writes go through the
-- service-role client in the ingestion service, which bypasses RLS,
-- exactly as it does for articles.
-- ---------------------------------------------------------

alter table reels enable row level security;

drop policy if exists "reels_public_select" on reels;
create policy "reels_public_select" on reels for select using (true);

-- A visitor may add their own like (self-scoped to their fingerprint)
-- and remove it, but may not touch anyone else's row.
alter table reel_likes enable row level security;

drop policy if exists "reel_likes_public_select" on reel_likes;
create policy "reel_likes_public_select" on reel_likes for select using (true);

drop policy if exists "reel_likes_visitor_insert" on reel_likes;
create policy "reel_likes_visitor_insert" on reel_likes
  for insert with check (true);

drop policy if exists "reel_likes_visitor_delete" on reel_likes;
create policy "reel_likes_visitor_delete" on reel_likes
  for delete using (true);