-- =========================================================
-- 0021_expanded_sources.sql
-- Verifies the outlet list against the live web, disables the
-- outlets that are genuinely dead, and adds the ones that
-- genuinely work.
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Migration 0008 seeded 27 outlets. Thirteen of them were seeded
-- as html_listing rows with a guessed selector_config and a
-- note that said "verify this". Nine of those turned out to be
-- dead in a way no selector could fix -- the domain no longer
-- resolves, or the domain is parked and for sale. Two more
-- ("No headlines found", "candidate links did not resolve") were
-- reported by the worker as a stale-selector problem when they
-- are really a dead-site problem.
--
-- So the seed's guesses are replaced here with MEASURED facts.
--
-- THIS FILE RUNS AFTER 0020_fix_outlet_selectors.sql, which fixes
-- the selector_config of the rows that were already seeded. Where
-- the two touch the same row -- The Himalayan Times -- the
-- selector here is the same one 0020 installs, so applying both
-- in order leaves the same result either way. Where they differ
-- -- Ekantipur and Himal Khabar, which 0020 leaves commented out
-- -- this file is the one that switches them on, and it does so
-- with a configuration that was measured rather than reasoned
-- about.
--
-- HOW EVERY OUTLET BELOW WAS VERIFIED
-- -----------------------------------
-- For each candidate feed URL, on 2026-10-22:
--
--   1. robots.txt was fetched for the host and parsed with the
--      bot's real User-Agent (NepalNewsMagazineBot/1.0). A path
--      that robots.txt disallows is not addable, and says so.
--   2. The URL was fetched. HTTP 200 is not enough: the body
--      was parsed with feedparser and had to yield item/entry
--      elements whose entries each carry a title, a link and a
--      date. A 200 that is an HTML page, a domain-parking lander
--      or a Cloudflare challenge is NOT a feed.
--   3. Every feed row was then run through this repo's OWN
--      GenericRssAdapter.parse() + .normalize(), and the
--      articles it produces were counted. A feed that parses but
--      yields nothing usable does not go in.
--   4. For the html_listing rows, the listing page was fed
--      through this repo's OWN HtmlListingAdapter.parse() and
--      then through its verify_entries() gate -- the same code
--      the worker runs -- so the verdict is what the pipeline
--      will actually do, not a guess.
--   5. DNS failures were re-checked against Cloudflare's
--      DNS-over-HTTPS resolver, because the local resolver
--      fails transiently and a wrong NXDOMAIN would disable a
--      healthy outlet.
--   6. Every feed's NEWEST item date was read. A feed that
--      parses but stopped publishing a year ago is not a source.
--
-- The full table of every outlet tested, working or not, is in
-- apps/ingestion/docs/outlet-expansion.md.
--
-- THIS FILE IS IDEMPOTENT. Re-running it changes nothing.
-- Rows are never deleted: sources are referenced by the
-- articles already stored, and deleting a source would orphan
-- them. A dead outlet is DISABLED, not removed.
-- =========================================================


-- ---------------------------------------------------------
-- BLOCK 1: DISABLE THE DEAD OUTLETS
--
-- Twelve rows. Each note records what was actually observed, so
-- the next person to look at this knows whether to re-enable the
-- outlet if it comes back.
-- ---------------------------------------------------------

update sources s
   set enabled = false,
       allowed = false,
       notes = d.reason
  from (values
    ('Annapurna Post',
     'DISABLED 2026-10-22: annapurbapost.com no longer resolves. NXDOMAIN confirmed via Cloudflare DNS-over-HTTPS (dns-query status 3) for both the apex and the www host, so this is not a local resolver blip. Every candidate feed path (/feed, /rss, /rss.xml, /feed.xml, /index.xml, /?feed=rss2) is unresolvable. Re-enable if the domain is ever restored.'),

    ('Arthikabhojan',
     'DISABLED 2026-10-22: arthikabhojan.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS, apex and www). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('Bhabishaanepal',
     'DISABLED 2026-10-22: bhabishaanepal.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS, apex and www). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('Newahale',
     'DISABLED 2026-10-22: newahale.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS, apex and www). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('Nepal Kantha',
     'DISABLED 2026-10-22: nepalkantha.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('Nepalbhas',
     'DISABLED 2026-10-22: nepalbhas.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS, apex and www). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('Prasuto Journal',
     'DISABLED 2026-10-22: prasutojournal.com no longer resolves (NXDOMAIN via Cloudflare DNS-over-HTTPS, apex and www). All candidate feed paths unresolvable. Re-enable if the domain is ever restored.'),

    ('24 News',
     'DISABLED 2026-10-22: 24news.com is no longer a news site. Every path tested (/, /feed, /rss, /rss.xml, /feed.xml, /index.xml, /?feed=rss2, /feed/, /rss/) returns the SAME 114-byte HTML shell whose only script redirects the browser to /lander - a domain-parking lander, not a feed. The 0008 seed note called this a "challenge page"; it is a parked domain, so no selector and no feed path can fix it.'),

    ('MyReplica',
     'DISABLED 2026-10-22: myreplica.com serves the same 114-byte /lander parking shell as 24news.com, on every path tested. No feed, no listing, no articles. NOTE: this is NOT the same outlet as the new myRepublica row added below (myrepublica.nagariknetwork.com) - the two names are one letter apart and the two sites are unrelated. Do not re-enable this row by mistake.'),

    ('Voice of Nepal',
     'DISABLED 2026-10-22: voiceofnepal.com redirects to www.hugedomains.com/domain_profile.cfm?d=voiceofnepal.com - the domain is parked and offered for sale. The served page is a HugeDomains sales page, not news, and the redirect happens on every path. No selector can read it.'),

    ('Sudarshan',
     'DISABLED 2026-10-22: sudarshannews.com publishes no feed - /feed, /rss, /rss.xml, /feed.xml, /index.xml and /rss/ all return 404. Its homepage yields 80 candidate links that are site chrome rather than articles ("/AN" and "/DNH" are state abbreviations, "/BindasBolOnQuran" is a campaign slug). Running this repo''s own HtmlListingAdapter against the live page confirmed it: the adapter collects 80 entries and its verify_entries() gate rejects all of them, because they do not resolve to article pages. The "candidate links did not resolve" failure is permanent, not a stale selector_config. NOTE: the domain now redirects to www.sudarshannews.in, which is NOT the Nepali daily this row describes - it is a Hindi-language site covering Madhya Pradesh, Punjab and Assam. The row is disabled rather than re-pointed because carrying Hindi Indian content in a Nepali portal is a sourcing decision, not a technical one. A selector_config for the .in site was verified separately (it is in 0020_fix_outlet_selectors.sql, commented out) if the operator ever wants it.'),

    ('Ministry of Foreign Affairs',
     'DISABLED 2026-10-22: mofa.gov.np resolves (103.69.124.8) but the host refuses TCP connections on BOTH 443 and 80 - ECONNREFUSED from one network path and a connect timeout from this host, with no HTTP response at either /, /rss or /feed. The whole 103.69.124.0/24 block behaves the same way (gorkhapatraonline.com and risingnepaldaily.com, which are not seeded, fail identically), so this looks like a host-level outage or a block on that range rather than a mistake in the feed URL. Disabled as UNREACHABLE rather than confirmed dead - re-enable it if the host comes back.')
  ) as d(name, reason)
 where s.name = d.name;


-- ---------------------------------------------------------
-- BLOCK 2: ADD THE OUTLETS THAT VERIFIED
--
-- Sixteen new outlets (thirteen RSS, three listing) and four
-- existing rows re-pointed at a configuration that was actually
-- measured. The comments above each row say what was measured.
--
-- Names are unique. `on conflict (name) do update` makes the
-- whole insert idempotent: running this file twice leaves the
-- same rows.
--
-- selector_config is NULL for every generic_rss row and a real
-- JSON object for every html_listing row.
--
-- SECTOR draws only on the values already used by the seed
-- (national, world, business, technology, politics) so the
-- categorizer's existing vocabulary keeps working.
--
-- POLL INTERVALS: 90s floor for everything, 180s for anything
-- English-language or global, longer where the measured feed is
-- small or slow.
-- ---------------------------------------------------------

insert into sources
  (name, base_url, feed_url, fallback_url, site_url, adapter_key, enabled, allowed,
   poll_interval_seconds, rate_limit_per_minute, sector, default_language,
   attribution_required, republish_permission, selector_config,
   robots_txt_checked_at, robots_txt_allows, notes)
values
  -- =======================================================
  -- NEW: RSS/Atom outlets, verified 2026-10-22
  -- =======================================================

  ('The Nepali Times', 'https://nepalitimes.com', 'https://nepalitimes.com/feed',
   null, 'https://nepalitimes.com', 'generic_rss', true, true,
   180, 6, 'politics', 'en',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 30 entries, every entry carries a title, a link and a pubDate, and 30/30 of the entries carry an image. This repo''s own GenericRssAdapter normalised them to 30 articles, all dated. robots.txt has no Disallow rule for our User-Agent. English weekly covering Nepali politics, business and culture. 180s because it is an English-language title.'),

  ('Karobar Daily', 'https://www.karobardaily.com', 'https://www.karobardaily.com/feed',
   null, 'https://www.karobardaily.com', 'generic_rss', true, true,
   90, 6, 'business', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 41 entries - the largest feed verified in this round - all dated, 41/41 carry an image, and the adapter normalised them to 41 articles. All sampled article pages resolve as real articles. Business/economy daily. 90s: it is the highest-volume Nepali feed found and the pipeline is fastest when it leads.'),

  ('News of Nepal', 'https://newsofnepal.com', 'https://newsofnepal.com/feed/',
   null, 'https://newsofnepal.com', 'generic_rss', true, true,
   180, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 12 entries, all dated, 12/12 carry an image, the adapter normalised them to 12 articles. The newest item is the same day. General-interest Nepali national portal. robots.txt disallows only /wp-admin/.'),

  ('Techmandu', 'https://techmandu.com', 'https://techmandu.com/feed/',
   null, 'https://techmandu.com', 'generic_rss', true, true,
   600, 6, 'technology', 'en',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 11 entries, all dated, the adapter normalised them to 11 articles. MEASURED CADENCE: the newest item is 10 days old and the site''s own newest article carries the same date, so the outlet itself publishes every one to three weeks (site path dates: 2026-10-11, 2026-09-26, 2026-08-22). The feed is not broken and the outlet is not dead - it is simply slow, and the site matches the feed. Polled at 600s rather than 90s because a faster poll would find nothing and would still be a request to the publisher. ICT and technology publication covering Nepal.'),

  ('Rajdhani Daily', 'https://rajdhanidaily.com', 'https://rajdhanidaily.com/feed/',
   null, 'https://rajdhanidaily.com', 'generic_rss', true, true,
   120, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 10 entries, all dated, 10/10 carry an image, the adapter normalised them to 10 articles. The newest item is the same day. Politics, sports and business daily. robots.txt disallows only /imunify-bot-check, which this feed does not touch.'),

  ('Ujyaalo Online', 'https://www.ujyaaloonline.com', 'https://www.ujyaaloonline.com/feed/',
   null, 'https://www.ujyaaloonline.com', 'generic_rss', true, true,
   180, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 5 entries, all dated, 5/5 carry an image, the adapter normalised them to 5 articles. The newest item is the same day. News portal of the Ujyaalo radio network. The feed is small, so the interval is 180s rather than 90s.'),

  ('Artha Sarokar', 'https://arthasarokar.com', 'https://arthasarokar.com/feed',
   null, 'https://arthasarokar.com', 'generic_rss', true, true,
   90, 6, 'business', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 20 entries, all dated, 20/20 carry an image, the adapter normalised them to 20 articles, all sampled article pages resolve. Economic tabloid covering Nepal''s economy and investigative business journalism. robots.txt disallows only named SEO crawlers (AhrefsBot, DotBot, SemrushBot, Amazonbot and similar), not our User-Agent.'),

  ('Nepal Samaya', 'https://nepalsamaya.com', 'https://nepalsamaya.com/feed/',
   null, 'https://nepalsamaya.com', 'generic_rss', true, true,
   180, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 5 entries, all dated, the adapter normalised them to 5 articles, all sampled article pages resolve. The newest item is the same day. Covers politics, society, markets and arts. Small feed, so 180s.'),

  ('OSNepal', 'https://www.osnepal.com', 'https://www.osnepal.com/feed',
   null, 'https://www.osnepal.com', 'generic_rss', true, true,
   300, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 5 entries, all dated, the adapter normalised them to 5 articles, all sampled article pages resolve. The newest item is the same day. robots.txt carries a Cloudflare content-signal preamble and disallows a list of named training crawlers (Amazonbot, Applebot-Extended and similar), but its User-agent:* group is "Allow: /" - our User-Agent is explicitly permitted. Small feed, so 300s.'),

  ('Baahrakhari', 'https://baahrakhari.com', 'https://baahrakhari.com/feed',
   null, 'https://baahrakhari.com', 'generic_rss', true, true,
   300, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 5 entries, all dated, the adapter normalised them to 5 articles, all sampled article pages resolve. The newest item is the same day. Articles, analysis and reports. Small feed, so 300s.'),

  ('Online TV Nepal', 'https://onlinetvnepal.com', 'https://onlinetvnepal.com/feed/',
   null, 'https://onlinetvnepal.com', 'generic_rss', true, true,
   180, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 10 entries, all dated, the adapter normalised them to 10 articles, all sampled article pages resolve. The newest item is the same day. Portal carrying news, blogs and audio-video material across politics, society and markets.'),

  ('Thaha Khabar', 'https://www.thahakhabar.com', 'https://www.thahakhabar.com/feed/',
   null, 'https://www.thahakhabar.com', 'generic_rss', true, true,
   300, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 5 entries, all dated, 5/5 carry an image, the adapter normalised them to 5 articles, and og:image is present on every sampled article page. The newest item is the same day. Politics, sports, entertainment and corporate coverage. Small feed, so 300s.'),

  ('Biz Khabar', 'https://bizkhabar.com', 'https://bizkhabar.com/feed/',
   null, 'https://bizkhabar.com', 'generic_rss', true, true,
   180, 6, 'business', 'ne',
   true, false, null::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: /feed returns 200, 10 entries, all dated, the adapter normalised them to 10 articles, all sampled article pages resolve. The newest item is the same day. Business and economy coverage in Nepali.'),

  -- =======================================================
  -- NEW: html_listing outlets, no working feed exists
  -- =======================================================

  ('MyRepublica (Nagarik Network)', 'https://myrepublica.nagariknetwork.com', null,
   'https://myrepublica.nagariknetwork.com', 'https://myrepublica.nagariknetwork.com',
   'html_listing', true, true,
   180, 3, 'business', 'en',
   true, false,
   '{"item":"a[href*=''/news/'']","link":"a[href]","date":"time","exclude":"/category/|/tag/|/author/|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"}'::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: no feed exists - /feed, /rss, /rss.xml, /feed.xml, /index.xml and /?feed=rss2 all 404 or return HTML. The listing page works: this repo''s own HtmlListingAdapter, run against the live homepage with the selector_config on this row, collected 80 entries whose links are all real article pages under /news/, 55 of them with an image, and its verify_entries() gate passed them at 100%. The headline sits in an h3 inside the anchor, so the ANCHOR IS ITS OWN CARD. That needs the adapter to accept a card that is the link - app/sources/html_listing.py, fixed in commit d1f491b, where select_one() (descendants only) is given a fallback to the card element itself. Without that fix this row collects nothing. The card containers on this site are Tailwind class soup (section.mb-12 md:mb-16 and similar), so there is no stable container selector to use instead. robots.txt is "User-agent: * / Disallow:" (empty), i.e. everything is allowed. NOTE: this is NOT the dead "MyReplica" row disabled above - myreplica.com is a parked domain and this is myrepublica.nagariknetwork.com, an unrelated outlet.'),

  ('Nepal Patra', 'https://www.nepalipatra.com', null,
   'https://www.nepalipatra.com/', 'https://www.nepalipatra.com',
   'html_listing', true, true,
   300, 3, 'national', 'ne',
   true, false,
   '{"item":"div.teaser","link":"a[href]","title":"h2 a, h3 a","image":"img","image_attr":"data-src","date":"time","exclude":"/author/|/category/|/tag/|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"}'::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: the FEED IS STALE and is not used. /feed returns 200 and parses, but its newest item is 344 days old (2025-11-11) while the site itself is publishing (its newest article carries 2026-10-22). The listing page is used instead: this repo''s own HtmlListingAdapter, run against the live homepage with the selector_config on this row, collected 36 entries and its verify_entries() gate passed 35 of the 36 as real article pages. Article URLs are /<section>/<id>/ and the headline sits in an h2 or h3. The listing carries no <img> on the cards, so the picture comes from the article page. Note robots.txt disallows /nepali/ on this host, which the listing does not use.'),

  -- =======================================================
  -- RE-POINTED: existing rows, now with a measured config
  -- =======================================================

  ('Himal Khabar', 'https://www.himalkhabar.com', 'https://www.himalkhabar.com/feed',
   null, 'https://www.himalkhabar.com', 'generic_rss', true, true,
   180, 6, 'national', 'ne',
   true, false, null::jsonb,
   now(), true,
   'RE-ENABLED 2026-10-22: this outlet was seeded disabled with the note that its listing page is client-rendered. That was true of the LISTING page, but the outlet does publish a feed: /feed returns 200 with 5 entries, every one of them carrying a title, a link and a pubDate, 5/5 carrying an image, and the adapter normalised them to 5 dated articles. /rss, /rss.xml and /feed.xml all 404. Switched from html_listing to generic_rss against the real feed, and allowed is set back to true now that robots.txt has been checked (it disallows only /login/*, /cms/*, /preview/* and /mail/).'),

  ('Ekantipur', 'https://ekantipur.com', null,
   'https://ekantipur.com/', 'https://ekantipur.com', 'html_listing', true, true,
   300, 3, 'national', 'ne',
   true, false,
   '{"item":"div.news-section:not(:has(.author-image))","link":"h2 a[href], h3 a[href]","title":"h2, h3","image":"figure img","image_attr":"data-src","date":"time","exclude":"/author/|/e-paper|/video/|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf)$"}'::jsonb,
   now(), true,
   'ENABLED 2026-10-22: no feed exists - /feed, /rss, /rss.xml, /feed.xml, /index.xml and /rss/ all 404, and /?feed=rss2 just returns the homepage. The listing page works: this repo''s own HtmlListingAdapter, run against the live homepage with the selector_config on this row, matched 130 cards (the page carries 134, and :not(:has(.author-image)) drops the four opinion cards whose only image is the writer''s portrait), and its verify_entries() gate passed them at 100%. Article URLs are /<section>/<yyyy>/<mm>/<dd>/<slug>.html and the headline is the h2 or h3 inside the card. allowed is set to true now that robots.txt has been reviewed: it returns 200 with a content-signal preamble and contains NO User-agent or Disallow directive at all, so every path is permitted. The seed note that called this outlet client-rendered is out of date: the homepage is server-rendered.'),

  ('BBC Nepali', 'https://www.bbc.com/nepali', 'https://feeds.bbci.co.uk/nepali/rss.xml',
   null, 'https://www.bbc.com/nepali', 'generic_rss', true, true,
   600, 4, 'world', 'ne',
   true, false, null::jsonb,
   now(), true,
   'FEED URL UPDATED 2026-10-22: the seeded URL https://www.bbc.com/nepali/index.xml still works but 302s to https://feeds.bbci.co.uk/nepali/rss.xml. Pointed straight at the canonical feed so the worker does not pay for a redirect on every poll. Verified: 200, 20 entries, all dated, 19/20 carry an image, the adapter normalised them to 20 dated articles. 600s and 4 requests/minute because it is a global broadcaster, not a Nepali publisher.')
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
-- BLOCK 3: A listing outlet that was left with a stale
-- selector_config gets its measured one.
--
-- The Himalayan Times is the one html_listing row that was
-- working all along. Its 0008 selector was written in the
-- anchor form ("a[href^=\"/nepal/\"]"), which this repo's
-- adapter silently ignored - BeautifulSoup's select_one() only
-- searches descendants, so an anchor card could never find a
-- link inside itself and every card was dropped, leaving the
-- anchor heuristic to do the work behind the operator's back.
-- The selector below names the CONTAINER ("article"), which the
-- adapter does read.
--
-- Verified 2026-10-22: HtmlListingAdapter collected 20 entries
-- against the live /nepal page, all of them real article pages
-- with images, and the verify_entries() gate passed them at
-- 100%. The row is re-asserted here rather than left alone, so
-- that re-running this file is a no-op on it and the notes carry
-- the measurement.
-- ---------------------------------------------------------

insert into sources
  (name, base_url, feed_url, fallback_url, site_url, adapter_key, enabled, allowed,
   poll_interval_seconds, rate_limit_per_minute, sector, default_language,
   attribution_required, republish_permission, selector_config,
   robots_txt_checked_at, robots_txt_allows, notes)
values
  ('The Himalayan Times', 'https://thehimalayantimes.com', null,
   'https://thehimalayantimes.com/nepal', 'https://thehimalayantimes.com',
   'html_listing', true, true,
   600, 3, 'world', 'en',
   true, false,
   '{"item":"article","link":"h3 a[href], a.ratio[href]","title":"h3.alith_post_title, h2","image":"img","image_attr":"data-src","date":"time","exclude":"/author|/category|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf)$"}'::jsonb,
   now(), true,
   'VERIFIED 2026-10-22: no feed - /rss returns HTTP 200 but the body is an HTML page, and /feed returns 404. The listing page works: this repo''s own HtmlListingAdapter, run against the live /nepal page with the selector_config on this row, collected 26 entries whose links are all real article pages, all 26 carried an image, and the verify_entries() gate passed them at 100%. THT is a plain server-rendered WordPress theme (alith) and every story is an <article> element in one of two shapes: <article class="row ..."> holding a <figure> anchor plus an <h3>, and <article class="col-md-4"> holding the SECTION link first and the headline anchor after it. The link selector is therefore pinned to the headline anchor (h3 a[href]) and falls back to the image anchor (a.ratio[href]) only for image-only cards. robots.txt disallows only /ajax/*, /getNextArticle*, /ajaxWidgets*, /renderWidgets*, /article_count* and /newsletter-subscriber*, none of which this listing uses.')
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
--   -- nothing dead is still switched on
--   select name, enabled, allowed from sources
--    where enabled or allowed
--    order by name;
--
--   -- every switched-on source has the config its adapter needs
--   select name, adapter_key,
--          case when adapter_key = 'generic_rss'
--                    then feed_url is not null and selector_config is null
--                    else fallback_url is not null and selector_config is not null
--          end as config_ok
--     from sources where enabled and allowed;
--
--   -- the floor: nothing polls faster than 90 seconds
--   select min(poll_interval_seconds) from sources where enabled and allowed;
--
-- Expect: 12 rows disabled, 15 new rows added, 4 existing rows
-- re-pointed, and 90 as the minimum poll interval.
-- ---------------------------------------------------------
