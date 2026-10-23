-- =========================================================
-- 0032_ask_the_refused_publishers_less_often.sql
--
-- Five Nepali publishers started refusing this worker with HTTP 403:
--
--   Ekantipur          403 for url 'https://ekantipur.com/'
--   Nepal News         403 for url 'https://nepalnews.com/feed?posts_per_page=100'
--   Nepal News English 403 for url 'https://english.nepalnews.com/feed?posts_per_page=100'
--   Ratopati           403 for url 'https://ratopati.com/feed'
--   Ujyaalo Online     403 for url 'https://www.ujyaaloonline.com/feed/'
--
-- WHAT WAS MEASURED FIRST, SO THIS IS NOT A GUESS
-- ------------------------------------------------
-- 1. THE FEED IS NOT THE PROBLEM. On 2026-10-23 every one of these
--    URLs was fetched from an ordinary client, four ways each:
--
--      the worker's own User-Agent (NepalNewsMagazineBot/1.0)  -> 200
--      a browser User-Agent (Chrome/124)                       -> 200
--      the browser User-Agent plus a Referer of the site's
--        own homepage                                            -> 200
--      Accept and Accept-Language a browser sends               -> 200
--
--    The bare feed URL, without ?posts_per_page, is 200 as well.
--    Ratopati redirects to www.ratopati.com/feed (200), the two
--    Nepal News feeds redirect to their trailing-slash form (200),
--    and Ekantipur has no feed at all: /feed, /rss, /rss.xml,
--    /feed.xml, /index.xml, /rss/, /en/feed and /api/feed all 404,
--    and /?feed=rss2 serves the homepage.
--
-- 2. ROBOTS.TXT PERMITS ALL OF THEM. ekantipur.com publishes no
--    User-agent or Disallow directive at all (every path allowed);
--    nepalnews.com and english.nepalnews.com disallow only
--    /wp-admin/; ujyaaloonline.com disallows only /login/*, /cms/*,
--    /preview/* and /mail/; ratopati.com allows everything and asks
--    for "Crawl-delay: 20" - which this worker had never read.
--
-- 3. SO THE REFUSAL IS ABOUT THE REQUEST PATTERN AND THE CLIENT,
--    NOT ABOUT THE PATH. Ekantipur is refused even by the request
--    that already carries a full Chrome User-Agent, and a full
--    poll-equivalent burst (1 feed + 12 article pages + 24 image
--    probes, 37 requests in 12 seconds) was answered 200, 37 times
--    over, from an ordinary client. What the production worker does
--    differently is that it asks EVERY publisher, from ONE address,
--    all day.
--
-- WHAT THIS MIGRATION CHANGES
-- ---------------------------
-- Ask these five publishers less often, and ask them the URL that
-- costs one request instead of two:
--
--   * every one of the four feeds redirects today, so every poll
--     cost a redirect PLUS the feed. The canonical URLs below are the
--     redirect targets and answer 200 directly;
--   * the intervals come down from 90s/180s to 300s, and the rate
--     limits from 6-8 a minute to 2-3 - ratopati.com's own
--     "Crawl-delay: 20" is 3 a minute, and the code now enforces it
--     as a floor however generous the row is;
--   * Ekantipur is polled every 900s and reads its listing from
--     /news instead of the homepage. This is the big one: a listing
--     outlet costs one page fetch per card plus up to three image
--     probes each, the homepage carries 134 cards (the adapter takes
--     80) and /news carries 31 - so this is a third of the requests
--     to a publisher that is refusing us, for the same stories.
--
-- Measured on 2026-10-23: /news returns 200 and the repo's own
-- HtmlListingAdapter, run against it with this row's
-- selector_config, collects 31 cards with real headlines and real
-- article links (measured with the same adapter, before and after).
--
-- The code that makes the request budget real is in this change
-- too: app/sources/base.py now keeps ONE robots.txt cache and ONE
-- request budget per host for the life of the process (both used to
-- be per-poll, so neither was ever really shared), and reads the
-- publisher's stated Crawl-delay.
--
-- Apply with:  supabase db push
--               (or psql -f this file against the project)
-- =========================================================

-- ---------------------------------------------------------
-- Nepal News (Nepali) and Nepal News English
-- ---------------------------------------------------------

update sources
   set poll_interval_seconds = 300,
       rate_limit_per_minute = 2,
       feed_url = 'https://nepalnews.com/feed/',
       robots_txt_allows = true,
       robots_txt_checked_at = now(),
       notes = coalesce(notes, '') ||
         ' POLITENESS 2026-10-23: this publisher refused the feed with HTTP 403 (21 refusals in the four days to 2026-10-23). Measured from an ordinary client the same URL answers 200 with the worker''s own User-Agent, with a browser User-Agent, with a Referer and with Accept-Language, so the refusal is not about the path or the headers - it is about how often this worker asks. Now polled every 300s at 2 requests a minute (was every 90s at 8). The feed URL is the canonical form, because https://nepalnews.com/feed 301s to it and every poll was paying for the redirect. robots.txt allows everything except /wp-admin/.'
 where name = 'Nepal News';

update sources
   set poll_interval_seconds = 300,
       rate_limit_per_minute = 2,
       feed_url = 'https://english.nepalnews.com/feed/',
       robots_txt_allows = true,
       robots_txt_checked_at = now(),
       notes = coalesce(notes, '') ||
         ' POLITENESS 2026-10-23: refused with HTTP 403 (19 refusals in the four days to 2026-10-23); measured 200 from an ordinary client with every header combination tried, so the refusal is about the request pattern, not the feed. Now polled every 300s at 2 requests a minute (was every 90s at 8), and pointed at the canonical URL (https://english.nepalnews.com/feed 301s to it). robots.txt allows everything except /wp-admin/.'
 where name = 'Nepal News English';

-- ---------------------------------------------------------
-- Ratopati
-- ---------------------------------------------------------

update sources
   set poll_interval_seconds = 300,
       rate_limit_per_minute = 3,
       feed_url = 'https://www.ratopati.com/feed',
       robots_txt_allows = true,
       robots_txt_checked_at = now(),
       notes = coalesce(notes, '') ||
         ' POLITENESS 2026-10-23: refused with HTTP 403 (21 refusals in the four days to 2026-10-23); measured 200 from an ordinary client with every header combination tried, so the refusal is about the request pattern, not the feed. ratopati.com publishes "Crawl-delay: 20" in its robots.txt - twenty seconds between requests - which this worker had never read and was in fact asking every 7.5 seconds. The Crawl-delay is now read and enforced as a floor, the row is polled every 300s at 3 requests a minute (was every 90s at 6), and the feed URL is the canonical www form (https://ratopati.com/feed 301s to it).'
 where name = 'Ratopati';

-- ---------------------------------------------------------
-- Ujyaalo Online
-- ---------------------------------------------------------

update sources
   set poll_interval_seconds = 300,
       rate_limit_per_minute = 2,
       feed_url = 'https://www.ujyaaloonline.com/feed',
       robots_txt_allows = true,
       robots_txt_checked_at = now(),
       notes = coalesce(notes, '') ||
         ' POLITENESS 2026-10-23: refused with HTTP 403 (8 refusals in the four days to 2026-10-23); measured 200 from an ordinary client with every header combination tried, so the refusal is about the request pattern, not the feed - the feed itself is small and healthy (5 entries, all dated, 5/5 with an image). Now polled every 300s at 2 requests a minute (was every 180s at 6), and pointed at the canonical URL (the trailing-slash form 301s to it). robots.txt allows everything except /login/*, /cms/*, /preview/* and /mail/.'
 where name = 'Ujyaalo Online';

-- ---------------------------------------------------------
-- Ekantipur
--
-- No feed exists (re-measured 2026-10-23: /feed, /rss, /rss.xml,
-- /feed.xml, /index.xml, /rss/, /en/feed and /api/feed all 404 and
-- /?feed=rss2 serves the homepage), so this outlet is read from its
-- listing page. The listing works from an ordinary client, but from
-- the production worker it has NEVER succeeded: 0 of the last 69
-- polls, and the "40/40 candidate links did not resolve" rows mean
-- the ARTICLE pages are refused too - the whole host is refusing this
-- client, not one path of it.
--
-- A listing outlet is the most expensive thing this worker does:
-- every card is a separate page fetch plus up to three image probes,
-- so the homepage (134 cards, the adapter takes 80) was asking
-- ekantipur.com for roughly two hundred requests every five minutes.
-- The section listing /news carries 31 of the same cards - the same
-- selector_config matches them - so this is a third of the requests
-- for the same stories.
-- ---------------------------------------------------------

update sources
   set poll_interval_seconds = 900,
       rate_limit_per_minute = 1,
       fallback_url = 'https://ekantipur.com/news',
       notes = coalesce(notes, '') ||
         ' POLITENESS 2026-10-23: still no feed - /feed, /rss, /rss.xml, /feed.xml, /index.xml, /rss/, /en/feed and /api/feed all 404 and /?feed=rss2 serves the homepage (re-measured 2026-10-23). The listing page is read from /news instead of the homepage: the same selector_config matches 31 cards there against 134 on the homepage (the adapter takes 80), and a listing costs one page fetch plus up to three image probes PER CARD, so this is a third of the requests for the same stories. Polled every 900s at 1 request a minute (was every 300s at 3). This outlet has never succeeded from the production worker - 0 of the last 69 polls - and the "40/40 candidate links did not resolve" rows show the ARTICLE pages are refused too, so the whole host is refusing this client rather than one path of it; if it stays silent, this row is the thing to disable, and the alternative is a different network path for the worker.'
 where name = 'Ekantipur';

-- ---------------------------------------------------------
-- Verify after applying
--
--   select name, poll_interval_seconds, rate_limit_per_minute, feed_url
--     from sources
--    where name in ('Nepal News', 'Nepal News English', 'Ratopati',
--                   'Ujyaalo Online', 'Ekantipur')
--    order by name;
--
-- Expect: Nepal News 300/2, Nepal News English 300/2, Ratopati 300/3,
-- Ujyaalo Online 300/2, Ekantipur 900/1.
--
-- And the feed URLs must NOT redirect any more:
--
--   select name, feed_url from sources
--    where adapter_key = 'generic_rss' and feed_url is not null;
--
-- Each of these four now answers 200 directly.
-- ---------------------------------------------------------
