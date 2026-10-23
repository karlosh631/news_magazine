-- =============================================================
-- 0030_hamropatro_news.sql
-- Add Hamro Patro's own news section as a source.
--
-- WHAT WAS FETCHED
-- ----------------
-- On 2026-10-23, with a browser-like User-Agent:
--
--   https://www.hamropatro.com/robots.txt   -> 200
--   https://www.hamropatro.com/news         -> 200, 433,932 bytes of HTML
--   https://www.hamropatro.com/news/national-> 200, 142,128 bytes of HTML
--
-- ROBOTS.TXT
-- ----------
-- The host publishes a robots.txt whose only group is "User-agent: *",
-- and it disallows exactly these paths:
--
--   /widgets/  /login  /en/login  /contest-term-condition
--   /terms  /privacy  /bookmark  /notes
--
-- /news and /news/national are NOT among them, so both are allowed.
-- (hamropatro.com is already used by this repo for the panchang and
-- the rashifal, whose paths are allowed by the same file.)
--
-- THERE IS NO FEED
-- ----------------
-- The news section publishes no RSS or Atom endpoint:
--
--   /news/feed  -> 200 but text/html - the Next.js app shell, not a feed
--   /news/rss   -> 200 but text/html - the same app shell
--   /rss  /rss.xml  /feed  /feed.xml -> 404
--
-- A 200 that serves HTML is not a feed, so the listing page is read
-- with the html_listing adapter.
--
-- HOW THE LISTING IS MARKED UP
-- ----------------------------
-- The page is server-rendered - the cards are in the served HTML, not
-- built by JavaScript - and every card is an ANCHOR of the site's own
-- `hp-card-surface` class:
--
--   <a class="hp-card-surface group flex gap-3 ..." href="/news/detail/4926905298350329-...">
--     <div class="aspect-square w-20 ...">
--       <img alt="" class="..." loading="lazy" src="https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/...">
--     </div>
--     <div class="flex min-w-0 flex-1 flex-col gap-1">
--       <span class="flex flex-wrap items-center gap-1.5 ...">
--         <span class="font-semibold text-primary">onlinekhabar.com</span>
--         <span aria-hidden="true">&middot;</span>
--         <span>३५ मिनेट अघि</span>
--       </span>
--       <h3 class="font-np font-bold ... line-clamp-2 text-title-sm">
--         इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?
--       </h3>
--       <p class="line-clamp-2 ...">६ कात्तिक, काठमाडौं । इजरायलमा ...</p>
--     </div>
--   </a>
--
-- The card therefore carries FOUR texts, and only the third is the
-- headline:
--
--   1. the name of the outlet the story was taken from
--      (onlinekhabar.com, ratopati.com, deshsanchar.com, ...)
--   2. a relative timestamp in Nepali prose ("३५ मिनेट अघि")
--   3. the headline, in the <h3>
--   4. the summary, in the <p>
--
-- Reading the anchor's own text would store all four, which is why
-- the row names an `h3` as the title and the card's own <img> as the
-- picture. The picture is served from image.hamropatro.com as a
-- resized, base64-encoded source URL, and it is in `src` - there is no
-- lazy-load attribute to read - so no `image_attr` is needed.
--
-- THE DATE
-- --------
-- The card's only date is the relative timestamp, and it is Nepali
-- prose rather than a machine-readable value. The page carries no
-- <time> element, no datetime attribute and no datePublished, and
-- the article page's own JSON-LD datePublished is the moment the
-- aggregator fetched the story, not the moment it was published.
-- So there is no date to read and the rows are stored with the time
-- they were fetched - the same situation as the MyRepublica row in
-- migration 0029.
--
-- MEASURED
-- --------
-- With this repo's own HtmlListingAdapter, run against the live
-- pages with the selector_config on these rows:
--
--   /news          26 entries in one run, 24 in an earlier run the
--                  same day - the page rotates as stories arrive -
--                  and 21 to 22 of them with the story's own picture
--   /news/national 34 entries in both runs, all 34 with a picture
--
-- In the first run, two of the 26 cards on /news were dropped by the
-- adapter's own headline filter, for reasons that are worth knowing
-- about and are NOT a broken selector: one headline contains
-- "फेसबुक", which the filter reads as a social-media link, and one
-- contains a semicolon, which it reads as a template artifact. Both
-- are real headlines. By the second run those two stories had rotated
-- off the page and all 26 cards were kept. They are recorded here
-- rather than worked around, because the filter lives in
-- app/sources/html_listing.py, which this work does not edit.
--
-- OVERLAP
-- -------
-- Hamro Patro's news pages syndicate from other Nepali outlets that
-- this pipeline already reads, and the two pages share stories with
-- each other. The deduper merges the copies (exact URL, then title,
-- then token fingerprint), so a story carried by four outlets is
-- stored once and counted as carried by four.
--
-- Only the headline and the picture are taken. republish_permission
-- stays false, as it does for every other outlet.
--
-- Idempotent: the rows are updated, and updating them twice is the
-- same as updating them once.
-- =============================================================

insert into sources
  (name, base_url, feed_url, fallback_url, site_url, adapter_key, enabled, allowed,
   poll_interval_seconds, rate_limit_per_minute, sector, default_language,
   attribution_required, republish_permission, selector_config,
   robots_txt_checked_at, robots_txt_allows, notes)
values
  -- =======================================================
  -- The news section itself: every section, newest first.
  -- =======================================================
  ('Hamro Patro News', 'https://www.hamropatro.com', null,
   'https://www.hamropatro.com/news', 'https://www.hamropatro.com/news',
   'html_listing', true, true,
   180, 3, 'national', 'ne',
   true, false,
   '{"item":"a.hp-card-surface[href*=''/news/detail/'']","link":"a[href]","title":"h3","image":"img","exclude":"/author/|/category/|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"}'::jsonb,
   now(), true,
   'VERIFIED 2026-10-23: robots.txt fetched for the host - the only group is "User-agent: *" and it disallows /widgets/, /login, /en/login, /contest-term-condition, /terms, /privacy, /bookmark and /notes, so /news is ALLOWED. No feed exists: /news/feed and /news/rss both answer 200 but serve the Next.js HTML app shell (text/html, no rss/feed elements), and /rss, /rss.xml, /feed and /feed.xml all 404. The listing page is server-rendered and works: this repo''s own HtmlListingAdapter, run against the live /news page with the selector_config on this row, collected 26 entries in a single request (24 in an earlier run the same day - the page rotates as stories arrive), 21 to 22 of them with the story''s own picture, and normalised them to that many articles. The card is the ANCHOR: <a class="hp-card-surface" href="/news/detail/<id>-<slug>"> holds the picture in an <img>, the headline in an <h3>, the summary in a <p>, and the outlet it came from plus a relative timestamp ("३५ मिनेट अघि") in a <span> above the headline - so the headline is read from the h3 and NOT from the anchor''s text, which would have stored the outlet name, the timestamp, the headline and the summary. No <time> element and no datePublished exist on the page, so there is no date to read and the articles are dated by the time they were fetched. 180s because the section refreshes on a minutes cadence. The cards carry the name of the outlet the story was taken from (onlinekhabar.com, ratopati.com, deshsanchar.com and others already in this pipeline); the deduper merges those copies.'),

  -- =======================================================
  -- The national section: the same markup, no mixing.
  -- =======================================================
  ('Hamro Patro National', 'https://www.hamropatro.com', null,
   'https://www.hamropatro.com/news/national', 'https://www.hamropatro.com/news/national',
   'html_listing', true, true,
   300, 3, 'national', 'ne',
   true, false,
   '{"item":"a.hp-card-surface[href*=''/news/detail/'']","link":"a[href]","title":"h3","image":"img","exclude":"/author/|/category/|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"}'::jsonb,
   now(), true,
   'VERIFIED 2026-10-23: the national section of the same news site, verified separately because it is the one every card on it belongs to - /news mixes the sections. robots.txt for the host disallows /widgets/, /login, /en/login, /contest-term-condition, /terms, /privacy, /bookmark and /notes, so /news/national is ALLOWED. No feed for this section either: /news/feed and /news/rss both answer 200 but serve the Next.js HTML app shell. The listing page works: this repo''s own HtmlListingAdapter, run against the live /news/national page with the selector_config on this row, collected 34 entries in a single request - every card on the page - and all 34 carry the story''s own picture, so the section is cleaner than /news (24 to 26 entries, 21 to 22 with a picture) because the brief lists have no pictures and the mixed page has a card shape without one. The card is the ANCHOR, exactly as on /news, and the headline is read from the <h3> rather than from the anchor''s text (which holds the outlet name, a relative Nepali timestamp, the headline and the summary). No <time> element and no datePublished, so the articles are dated by the time they were fetched. 300s, slower than /news, because the section is longer and its cards turn over less often. Overlaps with the Hamro Patro News row and with outlets already in this pipeline; the deduper merges the copies.')
on conflict (name) do update set
  base_url = excluded.base_url,
  feed_url = excluded.feed_url,
  fallback_url = excluded.fallback_url,
  site_url = excluded.site_url,
  adapter_key = excluded.adapter_key,
  enabled = excluded.enabled,
  allowed = excluded.allowed,
  poll_interval_seconds = excluded.poll_interval_seconds,
  rate_limit_per_minute = excluded.rate_limit_per_minute,
  sector = excluded.sector,
  default_language = excluded.default_language,
  attribution_required = excluded.attribution_required,
  republish_permission = excluded.republish_permission,
  selector_config = excluded.selector_config,
  robots_txt_checked_at = excluded.robots_txt_checked_at,
  robots_txt_allows = excluded.robots_txt_allows,
  notes = excluded.notes;


-- ---------------------------------------------------------
-- Verify after applying
--
--   -- the two rows are there, switched on, and pointed at a listing
--   select name, adapter_key, fallback_url, selector_config->>'item' as item
--     from sources where name like 'Hamro Patro%';
--
--   -- the floor: nothing polls faster than 90 seconds
--   select min(poll_interval_seconds) from sources where enabled and allowed;
--
-- Expect: 2 rows, both html_listing, with an item selector of
-- a.hp-card-surface[href*='/news/detail/'], and 90 as the minimum
-- poll interval.
-- ---------------------------------------------------------
