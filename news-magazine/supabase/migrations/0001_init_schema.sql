-- =========================================================
-- 0001_init_schema.sql
-- Nepal News & Magazine Platform — core schema
-- Auth is handled entirely by Supabase Auth (auth.users).
-- This migration only adds app-level tables that reference it.
-- =========================================================

create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;
create extension if not exists pg_trgm; -- fuzzy title matching for duplicate detection

-- ---------------------------------------------------------
-- Roles & profiles
-- ---------------------------------------------------------

create type app_role as enum (
  'super_admin',
  'admin',
  'editor',
  'journalist',
  'moderator',
  'advertiser',
  'registered_user'
);

create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url text,
  bio text,
  locale text not null default 'ne', -- 'ne' | 'en'
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table user_roles (
  user_id uuid not null references auth.users(id) on delete cascade,
  role app_role not null,
  granted_by uuid references auth.users(id),
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);

-- Convenience helper: does the current JWT belong to a user with role >= given level?
create or replace function has_role(min_roles app_role[])
returns boolean
language sql
security definer
stable
as $$
  select exists (
    select 1 from user_roles
    where user_id = auth.uid()
      and role = any(min_roles)
  );
$$;

-- ---------------------------------------------------------
-- Sources (admin-configurable ingestion sources)
-- ---------------------------------------------------------

create table sources (
  id uuid primary key default uuid_generate_v4(),
  name text not null unique,                 -- e.g. "Ekantipur"
  base_url text not null,
  feed_url text,                              -- RSS/Atom/API endpoint if permitted
  adapter_key text not null,                  -- maps to a Python adapter class, e.g. 'generic_rss'
  enabled boolean not null default false,     -- OFF by default until verified + approved
  allowed boolean not null default false,     -- explicit legal/ToS/robots.txt sign-off flag
  poll_interval_seconds integer not null default 900, -- 15 min default
  rate_limit_per_minute integer not null default 6,
  attribution_required boolean not null default true,
  republish_permission boolean not null default false, -- full-text republish allowed?
  default_category_id uuid,
  robots_txt_checked_at timestamptz,
  robots_txt_allows boolean,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table source_sync_logs (
  id uuid primary key default uuid_generate_v4(),
  source_id uuid not null references sources(id) on delete cascade,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running',     -- running | success | partial | failed
  fetched_count integer not null default 0,
  new_count integer not null default 0,
  duplicate_count integer not null default 0,
  rejected_count integer not null default 0,
  error_count integer not null default 0,
  details jsonb
);

create table source_errors (
  id uuid primary key default uuid_generate_v4(),
  source_id uuid not null references sources(id) on delete cascade,
  sync_log_id uuid references source_sync_logs(id) on delete cascade,
  occurred_at timestamptz not null default now(),
  error_type text not null,
  message text not null,
  context jsonb
);

-- ---------------------------------------------------------
-- Taxonomy
-- ---------------------------------------------------------

create table categories (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  name_en text not null,
  name_ne text,
  parent_id uuid references categories(id),
  sort_order integer not null default 0,
  created_at timestamptz not null default now()
);

alter table sources
  add constraint sources_default_category_fk
  foreign key (default_category_id) references categories(id);

create table tags (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  name text not null
);

create table authors (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  display_name text not null,
  bio text,
  avatar_url text,
  is_staff boolean not null default false,
  linked_user_id uuid references auth.users(id),
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------
-- Articles
-- ---------------------------------------------------------

create type article_status as enum (
  'ingested',
  'pending_review',
  'editor_review',
  'approved',
  'published',
  'scheduled',
  'archived',
  'rejected'
);

create table articles (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  headline text not null,
  subtitle text,
  language text not null default 'ne',        -- 'ne' | 'en'
  status article_status not null default 'ingested',
  is_breaking boolean not null default false,
  is_featured boolean not null default false,

  -- Content: full body ONLY populated when the platform owns the content
  -- or the source's republish_permission = true. Otherwise excerpt only.
  body_html text,
  excerpt text,

  featured_image_url text,
  featured_image_alt text,

  -- Attribution back to the original source (required whenever body_html
  -- is not the platform's own reporting)
  source_id uuid references sources(id),
  source_article_url text,
  source_name_snapshot text,

  primary_category_id uuid references categories(id),

  -- dedup fields
  canonical_url text,
  title_normalized text,
  content_hash text,

  published_at timestamptz,
  scheduled_for timestamptz,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  ingested_at timestamptz,

  view_count bigint not null default 0,

  search_vector tsvector
);

create unique index articles_content_hash_idx on articles (content_hash) where content_hash is not null;
create index articles_canonical_url_idx on articles (canonical_url);
create index articles_title_trgm_idx on articles using gin (title_normalized gin_trgm_ops);
create index articles_status_idx on articles (status);
create index articles_published_at_idx on articles (published_at desc);
create index articles_category_idx on articles (primary_category_id);
create index articles_search_idx on articles using gin (search_vector);

create table article_categories (
  article_id uuid not null references articles(id) on delete cascade,
  category_id uuid not null references categories(id) on delete cascade,
  primary key (article_id, category_id)
);

create table article_tags (
  article_id uuid not null references articles(id) on delete cascade,
  tag_id uuid not null references tags(id) on delete cascade,
  primary key (article_id, tag_id)
);

create table article_authors (
  article_id uuid not null references articles(id) on delete cascade,
  author_id uuid not null references authors(id) on delete cascade,
  primary key (article_id, author_id)
);

-- Track every source an article variant came from (for duplicate merges)
create table article_sources (
  id uuid primary key default uuid_generate_v4(),
  article_id uuid not null references articles(id) on delete cascade,
  source_id uuid not null references sources(id) on delete cascade,
  source_article_url text not null,
  fetched_at timestamptz not null default now(),
  is_primary boolean not null default false
);

create table media (
  id uuid primary key default uuid_generate_v4(),
  url text not null,
  media_type text not null default 'image', -- image | video | audio
  alt_text text,
  credit text,
  width integer,
  height integer,
  created_at timestamptz not null default now()
);

create table article_media (
  article_id uuid not null references articles(id) on delete cascade,
  media_id uuid not null references media(id) on delete cascade,
  sort_order integer not null default 0,
  primary key (article_id, media_id)
);

-- ---------------------------------------------------------
-- Engagement: comments, bookmarks, history, likes
-- ---------------------------------------------------------

create table comments (
  id uuid primary key default uuid_generate_v4(),
  article_id uuid not null references articles(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  parent_comment_id uuid references comments(id) on delete cascade,
  body text not null,
  status text not null default 'visible', -- visible | pending | rejected | deleted
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table comment_reports (
  id uuid primary key default uuid_generate_v4(),
  comment_id uuid not null references comments(id) on delete cascade,
  reported_by uuid not null references auth.users(id) on delete cascade,
  reason text,
  created_at timestamptz not null default now()
);

create table bookmarks (
  user_id uuid not null references auth.users(id) on delete cascade,
  article_id uuid not null references articles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, article_id)
);

create table reading_history (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  article_id uuid not null references articles(id) on delete cascade,
  read_at timestamptz not null default now(),
  read_seconds integer
);

create table likes (
  user_id uuid not null references auth.users(id) on delete cascade,
  article_id uuid not null references articles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, article_id)
);

-- ---------------------------------------------------------
-- Notifications & newsletter
-- ---------------------------------------------------------

create table notifications (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  type text not null, -- breaking_news | new_article | newsletter | system
  title text not null,
  body text,
  link_url text,
  read_at timestamptz,
  created_at timestamptz not null default now()
);

create table subscriptions (
  user_id uuid not null references auth.users(id) on delete cascade,
  channel text not null, -- breaking_news | daily_digest | category:<slug>
  created_at timestamptz not null default now(),
  primary key (user_id, channel)
);

create table newsletter_subscribers (
  id uuid primary key default uuid_generate_v4(),
  email text not null unique,
  user_id uuid references auth.users(id),
  confirmed boolean not null default false,
  confirm_token uuid default uuid_generate_v4(),
  subscribed_at timestamptz not null default now(),
  unsubscribed_at timestamptz
);

-- ---------------------------------------------------------
-- Advertisements
-- ---------------------------------------------------------

create table advertisement_campaigns (
  id uuid primary key default uuid_generate_v4(),
  advertiser_id uuid references auth.users(id),
  name text not null,
  starts_at timestamptz,
  ends_at timestamptz,
  enabled boolean not null default false,
  created_at timestamptz not null default now()
);

create table advertisements (
  id uuid primary key default uuid_generate_v4(),
  campaign_id uuid not null references advertisement_campaigns(id) on delete cascade,
  placement text not null, -- header_banner | homepage_top | article_top | sidebar | mobile_banner | ...
  image_url text not null,
  target_url text not null,
  impressions bigint not null default 0,
  clicks bigint not null default 0,
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------
-- Site config / pages / SEO
-- ---------------------------------------------------------

create table settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

create table pages (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  title text not null,
  body_html text,
  updated_at timestamptz not null default now()
);

create table menus (
  id uuid primary key default uuid_generate_v4(),
  key text not null unique, -- 'main_nav' | 'footer' | 'mobile_bottom_nav'
  items jsonb not null default '[]'
);

create table seo_metadata (
  id uuid primary key default uuid_generate_v4(),
  entity_type text not null, -- article | category | page
  entity_id uuid not null,
  meta_title text,
  meta_description text,
  og_image_url text,
  canonical_url text,
  unique (entity_type, entity_id)
);

-- ---------------------------------------------------------
-- Analytics & audit
-- ---------------------------------------------------------

create table analytics_events (
  id uuid primary key default uuid_generate_v4(),
  event_type text not null, -- view | search | share | click
  article_id uuid references articles(id) on delete set null,
  user_id uuid references auth.users(id) on delete set null,
  session_id text,
  metadata jsonb,
  occurred_at timestamptz not null default now()
);
create index analytics_events_type_time_idx on analytics_events (event_type, occurred_at desc);

create table article_views (
  id uuid primary key default uuid_generate_v4(),
  article_id uuid not null references articles(id) on delete cascade,
  viewed_at timestamptz not null default now(),
  referrer text,
  session_id text
);
create index article_views_article_time_idx on article_views (article_id, viewed_at desc);

create table audit_logs (
  id uuid primary key default uuid_generate_v4(),
  actor_id uuid references auth.users(id),
  action text not null,
  entity_type text,
  entity_id uuid,
  before jsonb,
  after jsonb,
  ip_address text,
  created_at timestamptz not null default now()
);

-- trending computed periodically into a materialized view (see 0003_functions_triggers.sql)
