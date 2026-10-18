-- =========================================================
-- 0009_article_sources_constraint.sql
-- Fixes the article_sources upsert that Stage 1 performed against a
-- unique constraint that was never declared.
--
-- services/ingestion.py upserted with
--     on_conflict="source_id,source_article_url"
-- but articles_sources (0001) declares only `id uuid primary key`, so
-- every call raised and the table stayed permanently empty. That is why
-- "reported by N outlets" never rendered and why the multi-source merge
-- record was never written.
--
-- Two things are wrong with the original intent and both are fixed:
--   - The conflict target should identify "this outlet carried this
--     story", which is (article_id, source_id). Keying on the article
--     URL meant the same story re-filed under a different URL by the
--     same outlet created a second, spurious link.
--   - (source_id, source_article_url) is still worth enforcing so one
--     outlet cannot file the same link twice.
-- =========================================================

-- Deduplicate before adding constraints: Stage 1's failures mean the
-- table is normally empty, but a partially-successful run may have left
-- duplicates behind and the unique index would otherwise fail to build.

delete from article_sources a
using article_sources b
where a.id > b.id
  and a.article_id = b.article_id
  and a.source_id = b.source_id;

delete from article_sources a
using article_sources b
where a.id > b.id
  and a.source_id = b.source_id
  and a.source_article_url = b.source_article_url;

create unique index if not exists article_sources_article_source_uniq
  on article_sources (article_id, source_id);

create unique index if not exists article_sources_url_uniq
  on article_sources (source_id, source_article_url);

-- Postgres needs a real constraint (not just a unique index) for
-- INSERT ... ON CONFLICT (cols) DO UPDATE to name it.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'article_sources_article_source_key'
  ) then
    alter table article_sources
      add constraint article_sources_article_source_key
      unique (article_id, source_id);
  end if;
end;
$$;

-- ---------------------------------------------------------
-- ATOMIC SOURCE COUNT
--
-- "Reported by N outlets" has to be a real counter. A read-then-write
-- from Python races: two sources polled concurrently both read 1, both
-- write 2, and one increment is lost.
-- ---------------------------------------------------------

create or replace function increment_article_source_count(p_article_id uuid)
returns integer
language sql
volatile
security definer
set search_path = public
as $$
  update articles
  set source_count = greatest(coalesce(source_count, 1), 1) + 1,
      last_seen_at = now()
  where id = p_article_id
  returning source_count;
$$;

comment on function increment_article_source_count is
  'Atomically bumps articles.source_count when another outlet is found carrying the same story.';

grant execute on function increment_article_source_count(uuid) to service_role;