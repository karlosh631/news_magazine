-- =========================================================
-- 0003_functions_triggers.sql
-- =========================================================

-- Generic updated_at maintenance
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trg_profiles_updated_at before update on profiles
  for each row execute function set_updated_at();
create trigger trg_sources_updated_at before update on sources
  for each row execute function set_updated_at();
create trigger trg_articles_updated_at before update on articles
  for each row execute function set_updated_at();
create trigger trg_comments_updated_at before update on comments
  for each row execute function set_updated_at();

-- Full-text search vector (headline weighted higher than excerpt/body)
create or replace function articles_update_search_vector()
returns trigger language plpgsql as $$
begin
  new.search_vector :=
    setweight(to_tsvector('simple', coalesce(new.headline, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(new.subtitle, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(new.excerpt, '')), 'C');
  return new;
end;
$$;
-- 'simple' config is used instead of 'english' because a large share of
-- content is Nepali (Devanagari); 'english' stemming does not apply and
-- would silently degrade Nepali search. Swap to a language-aware config
-- if/when migrating to Meilisearch/Elasticsearch per the search API design.

create trigger trg_articles_search_vector
  before insert or update of headline, subtitle, excerpt on articles
  for each row execute function articles_update_search_vector();

-- Atomic view counter (call from the article page server action / API route)
create or replace function increment_article_view(p_article_id uuid, p_referrer text default null, p_session_id text default null)
returns void language plpgsql security definer as $$
begin
  update articles set view_count = view_count + 1 where id = p_article_id;
  insert into article_views (article_id, referrer, session_id)
  values (p_article_id, p_referrer, p_session_id);
end;
$$;

-- Trending: materialized view over the last 48h of views, refreshed on a
-- schedule (pg_cron or an admin/cron-triggered API route).
create materialized view trending_articles as
select
  a.id as article_id,
  count(v.id) as views_48h
from articles a
join article_views v on v.article_id = a.id and v.viewed_at > now() - interval '48 hours'
where a.status = 'published'
group by a.id
order by views_48h desc
limit 50;

create unique index trending_articles_article_id_idx on trending_articles (article_id);

create or replace function refresh_trending_articles()
returns void language plpgsql security definer as $$
begin
  refresh materialized view concurrently trending_articles;
end;
$$;

-- Breaking news: simple view, no separate table needed beyond the flag
create view breaking_news as
select * from articles
where status = 'published' and is_breaking = true
order by published_at desc;
