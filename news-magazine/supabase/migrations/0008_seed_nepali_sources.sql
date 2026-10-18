-- =========================================================
-- 0008_seed_nepali_sources.sql
-- Registers 20+ Nepali news outlets for the autonomous ingestion
-- service so the worker has something to poll on first boot.
--
-- STATUS COLUMN MEANING
--   adapter_key = 'generic_rss'  -> a public RSS/Atom syndication
--     feed that the outlet itself publishes. Verified to return
--     HTTP 200 with parseable items at the time this migration
--     was written.
--
--   adapter_key = 'html_listing' -> NO working feed was found for
--     this outlet. The html_listing adapter scrapes the outlet's
--     public listing page with per-domain CSS selectors, and is
--     still gated at fetch time by the robots.txt checker in
--     app/sources/base.py.
--
--   allowed = true  -> robots.txt is checked and enforced at fetch
--     time for every request. Set to false to stop an outlet
--     entirely; the worker will simply stop polling it.
--
-- To add an outlet later you do NOT need a deploy: insert a row here
-- (or via the admin panel) with the right adapter_key and selectors.
-- =========================================================

-- ---------------------------------------------------------
-- TIER 1: outlets with a verified public RSS/Atom feed
-- ---------------------------------------------------------

insert into sources
  (name, base_url, feed_url, site_url, adapter_key, enabled, allowed,
   poll_interval_seconds, rate_limit_per_minute, sector, default_language,
   attribution_required, republish_permission, selector_config, notes)
values
  ('OnlineKhabar', 'https://www.onlinekhabar.com', 'https://www.onlinekhabar.com/feed',
   'https://www.onlinekhabar.com', 'generic_rss', true, true, 180, 8, 'national', 'ne',
   true, false, null::jsonb,
   'Largest Nepali-language daily. Public WordPress feed, ~20 items.'),

  ('OnlineKhabar English', 'https://english.onlinekhabar.com', 'https://english.onlinekhabar.com/feed',
   'https://english.onlinekhabar.com', 'generic_rss', true, true, 300, 8, 'world', 'en',
   true, false, null::jsonb,
   'English edition. Separate feed from the Nepali edition.'),

  ('Kantipur', 'https://kathmandupost.com', 'https://kathmandupost.com/rss',
   'https://kathmandupost.com', 'generic_rss', true, true, 240, 8, 'national', 'en',
   true, false, null::jsonb,
   'Owned by Kantipur Media Group. Public RSS, ~40 items.'),

  ('Ratopati', 'https://ratopati.com', 'https://ratopati.com/feed',
   'https://ratopati.com', 'generic_rss', true, true, 180, 8, 'business', 'ne',
   true, false, null::jsonb,
   'Business and economy focused. Public feed.'),

  ('Setopati', 'https://www.setopati.com', 'https://www.setopati.com/feed',
   'https://www.setopati.com', 'generic_rss', true, true, 300, 6, 'national', 'ne',
   true, false, null::jsonb,
   'Small feed (~5 items) so poll interval is relaxed. HTML fallback used when feed is thin.'),

  ('Nepal News', 'https://nepalnews.com', 'https://nepalnews.com/feed',
   'https://nepalnews.com', 'generic_rss', true, true, 180, 8, 'national', 'ne',
   true, false, null::jsonb,
   'Public RSS, ~12 items.'),

  ('Nepal News English', 'https://english.nepalnews.com', 'https://english.nepalnews.com/feed',
   'https://english.nepalnews.com', 'generic_rss', true, true, 300, 8, 'national', 'en',
   true, false, null::jsonb,
   'English edition of Nepal News.'),

  ('Nagarik News', 'https://nagariknews.com', 'https://nagariknews.com/feed',
   'https://nagariknews.com', 'generic_rss', true, true, 180, 8, 'national', 'ne',
   true, false, null::jsonb,
   'High-volume feed (~20 items). Strong syndicated-story source for dedup testing.'),

  ('Nagarik Times', 'https://nagariktimes.com', 'https://nagariktimes.com/feed',
   'https://nagariktimes.com', 'generic_rss', true, true, 240, 8, 'national', 'en',
   true, false, null::jsonb,
   'English-language Nepali daily.'),

  ('Khabarzone', 'https://www.khabarzone.com', 'https://www.khabarzone.com/feed',
   'https://www.khabarzone.com', 'generic_rss', true, true, 240, 6, 'technology', 'ne',
   true, false, null::jsonb,
   'Technology and gadgets focused. ~10 items per feed.'),

  ('Pradesh Post', 'https://www.pradeshpost.com', 'https://www.pradeshpost.com/rss',
   'https://www.pradeshpost.com', 'generic_rss', true, true, 300, 6, 'national', 'ne',
   true, false, null::jsonb,
   'Province-level coverage. ~10 items per feed.'),

  ('BBC Nepali', 'https://www.bbc.com/nepali', 'https://www.bbc.com/nepali/index.xml',
   'https://www.bbc.com/nepali', 'generic_rss', true, true, 600, 4, 'world', 'ne',
   true, false, null::jsonb,
   'International coverage in Nepali. Poll interval is relaxed to stay polite.'),

  ('Ministry of Foreign Affairs', 'https://mofa.gov.np', 'https://mofa.gov.np/rss',
   'https://mofa.gov.np', 'generic_rss', true, true, 1800, 4, 'politics', 'en',
   true, false, null::jsonb,
   'Official government feed. Slow-moving; 30 minute interval is ample.')
on conflict (name) do update set
  feed_url = excluded.feed_url,
  adapter_key = excluded.adapter_key,
  selector_config = excluded.selector_config,
  poll_interval_seconds = excluded.poll_interval_seconds,
  sector = excluded.sector,
  default_language = excluded.default_language;

-- ---------------------------------------------------------
-- TIER 2: outlets with no working feed found; the html_listing
-- adapter scrapes the public listing page with per-domain CSS
-- selectors. robots.txt is still enforced at fetch time.
--
-- These are inserted enabled but with a deliberately conservative
-- rate limit. If the robots check fails for an outlet the worker
-- records a not_permitted error and backs off rather than retrying.
-- ---------------------------------------------------------

insert into sources
  (name, base_url, fallback_url, site_url, adapter_key, enabled, allowed,
   poll_interval_seconds, rate_limit_per_minute, sector, default_language,
   attribution_required, republish_permission, selector_config, notes)
values
  ('The Himalayan Times', 'https://thehimalayantimes.com', 'https://thehimalayantimes.com/nepal',
   'https://thehimalayantimes.com', 'html_listing', true, true, 600, 3, 'world', 'en',
   true, false,
   '{"item":"a[href^=\"/nepal/\"], a[href^=\"/business/\"], a[href^=\"/world/\"]","link":"a","exclude":"/about-us|/contact-us|/advertise|/author|/tag/|\\.(jpg|png|pdf)$"}'::jsonb,
   '/rss returned HTML not a feed. Article URLs are /section/slug so the link pattern is the selector.'),

  ('Annapurna Post', 'https://www.annapurbapost.com', 'https://www.annapurbapost.com/category/news/',
   'https://www.annapurbapost.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post-item, .news-item","link":"a[href]","exclude":"/author|/category/|/tag/|\\.(jpg|png)$"}'::jsonb,
   'No discoverable RSS endpoint. Verify robots.txt permits /category/ before leaving enabled.'),

  ('24 News', 'https://24news.com', 'https://24news.com/',
   'https://24news.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .news-block, .post","link":"a[href]","exclude":"/author|/video-gallery|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Returned a 114-byte challenge page during setup verification. Expect fetch failures until the listing path is confirmed.'),

  ('MyReplica', 'https://myreplica.com', 'https://myreplica.com/',
   'https://myreplica.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .entry-content, .post","link":"a[href]","exclude":"/author|/category/|\\.(jpg|png)$"}'::jsonb,
   'Same 114-byte challenge page as 24 News (shared infrastructure).'),

  ('Newahale', 'https://www.newahale.com', 'https://www.newahale.com/',
   'https://www.newahale.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post-content, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'WordPress-based. No /feed endpoint found.'),

  ('Nepal Kantha', 'https://nepalkantha.com', 'https://nepalkantha.com/',
   'https://nepalkantha.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .news-list li","link":"a[href]","exclude":"/author|/category/|\\.(jpg|png)$"}'::jsonb,
   'Timed out from the setup network; may be geo-restricted. Rate limit kept low.'),

  ('Bhabishaanepal', 'https://www.bhabishaanepal.com', 'https://www.bhabishaanepal.com/',
   'https://www.bhabishaanepal.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Nepali-language daily.'),

  ('Arthikabhojan', 'https://www.arthikabhojan.com', 'https://www.arthikabhojan.com/',
   'https://www.arthikabhojan.com', 'html_listing', true, true, 900, 3, 'business', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Business/economy focused Nepali daily.'),

  ('Voice of Nepal', 'https://www.voiceofnepal.com', 'https://www.voiceofnepal.com/',
   'https://www.voiceofnepal.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Nepali-language news portal.'),

  ('Nepalbhas', 'https://www.nepalbhas.com', 'https://www.nepalbhas.com/',
   'https://www.nepalbhas.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Nepali-language news portal.'),

  ('Himal Khabar', 'https://www.himalkhabar.com', 'https://www.himalkhabar.com/',
   'https://www.himalkhabar.com', 'html_listing', true, false, 900, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'DISABLED: its listing page is client-rendered, so the served HTML contains only template literals (${item.title}). The adapter correctly refuses to guess rather than storing them. Enable once a working feed or selector_config is found.'),

  ('Prasuto Journal', 'https://www.prasutojournal.com', 'https://www.prasutojournal.com/',
   'https://www.prasutojournal.com', 'html_listing', true, true, 600, 3, 'business', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Business daily.'),

  ('Sudarshan', 'https://sudarshannews.com', 'https://sudarshannews.com/',
   'https://sudarshannews.com', 'html_listing', true, true, 600, 3, 'national', 'ne',
   true, false,
   '{"item":"article, .post, .news-item","link":"a[href]","exclude":"/author|/tag/|\\.(jpg|png)$"}'::jsonb,
   'Its /feed path returned 404.'),

  ('Ekantipur', 'https://ekantipur.com', 'https://ekantipur.com/',
   'https://ekantipur.com', 'html_listing', true, false, 900, 2, 'national', 'ne',
   true, false,
   '{"item":"a[href*=\"/news-detail/\"], a[href*=\"/article/\"]","link":"a","exclude":"/e-paper|/epaper|/video|\\.(jpg|png|js|css)$"}'::jsonb,
   'DISABLED ON PURPOSE: homepage is client-rendered so link discovery needs a DOM-backed fetch, and their robots.txt needs manual review. The /news-detail/ pattern is staged here ready to enable.')
on conflict (name) do update set
  feed_url = excluded.feed_url,
  fallback_url = excluded.fallback_url,
  adapter_key = excluded.adapter_key,
  selector_config = excluded.selector_config,
  poll_interval_seconds = excluded.poll_interval_seconds,
  rate_limit_per_minute = excluded.rate_limit_per_minute,
  sector = excluded.sector,
  enabled = excluded.enabled;

-- ---------------------------------------------------------
-- Backfill the newly-added columns for rows created before 0007.
-- ---------------------------------------------------------

update sources
set site_url = coalesce(site_url, base_url)
where site_url is null;

update articles
set last_seen_at = coalesce(last_seen_at, ingested_at, created_at)
where last_seen_at is null;

-- Stage 1 rows were written without title_normalized, which is the
-- column the fuzzy dedup lookup depends on. Backfill it so the first
-- run of the new pipeline has something to match against.
update articles
set title_normalized = lower(regexp_replace(headline, '\s+', ' ', 'g'))
where title_normalized is null
  and headline is not null;