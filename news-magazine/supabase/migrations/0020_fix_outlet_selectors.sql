-- =========================================================
-- 0020_fix_outlet_selectors.sql
-- Operator-applied fix for the html_listing outlets that were
-- failing in the admin dashboard's error log. Run this by hand:
--
--     psql "$SUPABASE_DB_URL" -f 0020_fix_outlet_selectors.sql
--
-- or paste the UPDATE blocks into the Supabase SQL editor.
--
-- HOW A selector_config IS READ
--   sources.selector_config is jsonb with these keys, all optional
--   except `item`:
--
--     "item"        CSS selector for ONE card. Every matching
--                   element is treated as a separate story, so it
--                   must be the smallest element that wraps a
--                   single headline.
--     "link"        CSS selector for the anchor INSIDE the card.
--                   This is select_one(), i.e. descendants only --
--                   it does NOT match the card element itself. A
--                   card that IS an <a> therefore yields nothing.
--                   Default: "a[href]".
--     "title"       CSS selector for the headline inside the card.
--                   Falls back to the link's text, then its
--                   title="" attribute.
--     "image"       CSS selector for the image inside the card.
--                   Default "img". Set to "" to skip listing
--                   thumbnails and let the article-page fetch
--                   supply og:image instead.
--     "image_attr"  Attribute to prefer over src. Use for lazy
--                   loaders (data-src, data-original).
--     "date"        CSS selector for a <time> inside the card.
--     "exclude"     Regex. An href that matches is dropped. There
--                   is no "include" key, so an allow-list is
--                   written as a negative lookahead (see Sudarshan).
--
-- *** "date" IS NOT OPTIONAL DESPITE LOOKING IT ***
--   html_listing.py does `if config.get("date", "time"):` and then
--   `card.select_one(config["date"])`. When the key is ABSENT the
--   first line yields the default "time" and the second line raises
--   KeyError: 'date'. Every config below therefore carries an
--   explicit "date" key. Do not strip it when editing.
--
-- WHAT "did not resolve to an article page" MEANS
--   verify_entries() GETs each discovered URL and requires:
--   HTTP < 400, final path != "", body >= 500 bytes, and an <h1>.
--   More than half the candidates failing raises and the poll is
--   abandoned. An outlet whose article pages carry no <h1> can
--   therefore never pass, however good its selectors are.
--
-- Only outlets with a VERIFIED selector appear below. Outlets
-- whose domain is parked, for sale, or simply unreachable are
-- listed as commented-out `enabled = false` recommendations
-- instead, because no selector_config can rescue them.
-- =========================================================


-- ---------------------------------------------------------
-- FIX 1: The Himalayan Times  --  ACTIVE
--
-- Was: '{"item":"a[href^=\"/nepal/\"], a[href^=\"/business/\"], a[href^=\"/world/\"]",
--        "link":"a", ...}'
-- Why it failed: `item` matched the article anchor ITSELF, then
--   `link: "a"` asked for an anchor INSIDE it. css select_one()
--   only searches descendants, so every card returned None and
--   every story was discarded. The adapter fell through to the
--   anchor heuristic, which scraped site chrome -- the dashboard
--   reported 25/36 links not resolving, and the one entry that
--   survived pointed at /morearticles/Nepal.
-- What it matches now: THT is a plain server-rendered WordPress
--   theme (alith). Every story on /nepal is an <article> element
--   in one of two shapes:
--     <article class="row ...">    <figure> <a class="ratio"> + <h3>
--     <article class="col-md-4">   <a class="meta_section"> + <a class="ratio"> + <h3>
--   The second shape puts the section link (/nepal, /opinion)
--   FIRST, so `link` is pinned to the headline anchor and falls
--   back to the image anchor only for image-only cards.
-- Verified: 26 cards parsed, 26/26 probe clean (repeatable),
--   images resolve (cdnx.premiumread.com returns 200 image/webp).
-- ---------------------------------------------------------

update sources
set selector_config = '{"item":"article","link":"h3 a[href], a.ratio[href]","title":"h3.alith_post_title, h2","image":"img","image_attr":"data-src","date":"time","exclude":"/author|/category|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf)$"}'::jsonb
where name = 'The Himalayan Times';


-- ---------------------------------------------------------
-- FIX 2: Sudarshan  --  ACTIVE SELECTOR, BUT READ THIS FIRST
--
-- Two separate problems, only one of which is a selector.
--
-- (a) The DOMAIN IS NO LONGER THE NEPALI OUTLET. sudarshannews.com
--     now 301s to www.sudarshannews.in, which is "Sudarshn News",
--     a HINDI-language site covering India -- Madhya Pradesh,
--     Punjab, Assam. It is not the Nepali daily the sources row
--     describes, and it is not in Nepali. The row still says
--     default_language = 'ne' and sector = 'national', which would
--     be wrong for what the page actually serves.
--
-- (b) THE SELECTOR ITSELF. Was: '{"item":"article, .post, .news-item",
--     "link":"a[href]", ...}'. Those class names appear nowhere on
--     the site, so no card matched, the adapter fell through to the
--     anchor heuristic and collected 40 chrome links (/AN, /DNH,
--     /BindasBolOnQuran, /CG, /GJ ...) -- every one a 404 or a
--     listing page. Hence "40/40 candidate links did not resolve".
--
-- The selectors below are VERIFIED against sudarshannews.in:
--   40 cards parsed, 39/40 probe clean. Cards live in
--   div.newssublist / div.newssublistnimg / div.trennewdata /
--   div.livenewlist, plus an <h2>/<h3> block in .bannewboxdata.
--   Articles are ASP.NET: <slug>-<id>-newsdetails.aspx.
--   There is no "include" key, so `exclude` is written as a
--   negative lookahead that drops EVERY href lacking
--   newsdetails.aspx -- that is what removes /AN, /DNH,
--   /BindasBolOnQuran and the /bg- blog slugs.
--
-- This UPDATE fixes the selector only. If you run it, also run the
-- two statements below it so the row matches reality. If you would
-- rather not carry Hindi content in a Nepali portal, disable the
-- source instead (last statement in this block).
--
-- No thumbnails: the site puts artwork in a CSS
-- background-image on div.newlistimg, which html_listing cannot
-- read. Articles still get an image from og:image when the
-- pipeline fetches the page.
-- ---------------------------------------------------------

update sources
set selector_config = '{"item":"div.newssublist, div.newssublistnimg, div.trennewdata, div.livenewlist, .bannewboxdata h2, .bannewboxdata h3","link":"a[href]","title":"p, h2, h3","image":"img","image_attr":"data-src","date":"time","exclude":"^(?!.*newsdetails\\.aspx)"}'::jsonb
where name = 'Sudarshan';

-- OPTIONAL, operator's call: follow the domain the outlet moved to,
-- and label the row for what it actually serves.
-- update sources
-- set fallback_url     = 'https://www.sudarshannews.in/',
--     base_url         = 'https://www.sudarshannews.in',
--     default_language = 'hi',
--     sector           = 'international',
--     notes            = 'sudarshannews.com now redirects to sudarshannews.in, a HINDI-language Indian outlet. Not the Nepali daily this row was seeded for. Selector verified 2026-10-22; articles end in -newsdetails.aspx.'
-- where name = 'Sudarshan';

-- ALTERNATIVE, if Hindi content does not belong in the portal:
-- update sources set enabled = false where name = 'Sudarshan';


-- =========================================================
-- OUTLETS THAT NO SELECTOR_CONFIG CAN FIX
--
-- Each of these is listed in the dashboard's error log, and each
-- was fetched by hand on 2026-10-22. The listings are not
-- merely mis-selected: the domains are gone. Re-pointing a
-- selector at a domain-parking page produces the same error
-- with a different message. The statements are commented out
-- because switching an outlet off is the operator's decision,
-- not this migration's.
-- =========================================================


-- ---------------------------------------------------------
-- 24 News  --  DOMAIN PARKED AND FOR SALE
--
-- robots.txt is permissive ("User-agent: * / Allow: /"), so this
-- was never a permissions problem. https://24news.com/ returns a
-- 114-byte stub that does window.location = "/lander"; /lander
-- answers 307 -> https://forsale.godaddy.com/forsale/24news.com.
-- There is no news content at any path. The seed note calling
-- this a "114-byte challenge page" was a misreading: it is a GoDaddy
-- parking redirect, not bot protection.
--
-- (For the record: the Nepali broadcaster "News 24" is at
-- news24nepal.com -- a different channel on a different domain.
-- Swapping it in here is a sourcing decision, so no such statement
-- is pre-written below.)
--
-- update sources set enabled = false where name = '24 News';


-- ---------------------------------------------------------
-- MyReplica  --  DOMAIN PARKED AND FOR SALE
--
-- Identical to 24 News, down to the shared parking infrastructure
-- the seed note suspected: 13.248.169.48 / 76.223.54.146
-- (GoDaddy). / returns the same 114-byte stub, /lander 307s to
-- https://forsale.godaddy.com/forsale/myreplica.com.
-- Same robots.txt, same verdict.
--
-- update sources set enabled = false where name = 'MyReplica';


-- ---------------------------------------------------------
-- Voice of Nepal  --  DOMAIN FOR SALE
--
-- https://www.voiceofnepal.com/ answers 302 ->
-- https://www.hugedomains.com/domain_profile.cfm?d=voiceofnepal.com,
-- a parked "voiceofnepal.com is for sale" page behind a captcha.
-- No robots.txt, no content. Unrelated to the 24 News / MyReplica
-- parking above -- this one is HugeDomains, not GoDaddy.
--
-- update sources set enabled = false where name = 'Voice of Nepal';


-- ---------------------------------------------------------
-- Ministry of Foreign Affairs  --  HOST UNREACHABLE (generic_rss)
--
-- Not a selector problem: this row uses generic_rss, and
-- selector_config is never read by that adapter. DNS resolves
-- (mofa.gov.np -> 103.69.124.8) but TCP connect to both :80 and
-- :443 times out after ~21s from the ingestion host. The
-- RobotsChecker treats an unreachable robots.txt as "no rules"
-- and lets the request through, so the poll dies on a connect
-- timeout and lands in the log as fetch_or_parse_error rather
-- than dns_failure.
--
-- If the government site is blocked outside Nepal, disabling is
-- the only honest answer. If it is simply down, leave it enabled
-- and it will recover on its own.
--
-- update sources set enabled = false where name = 'Ministry of Foreign Affairs';


-- =========================================================
-- CANDIDATES FOR RE-ENABLING
--
-- Sources that are currently allowed = false / enabled = false
-- and were checked by hand on 2026-10-22. Every statement below
-- is commented out. Turning an outlet on is the operator's
-- decision: it means asserting that the outlet's terms permit
-- this kind of automated access.
-- =========================================================


-- ---------------------------------------------------------
-- Ekantipur  --  selector VERIFIED, but one code change away
--
-- The homepage is server-rendered after all (the seed note that
-- called it client-rendered is out of date). Cards are
-- div.news-section; :has(.author-image) drops the four opinion
-- cards whose only image is the writer's portrait, which would
-- otherwise be stored as the story thumbnail.
-- Verified: 133 cards parsed, every link a real article URL of
-- the form /<section>/YYYY/MM/DD/<slug>-N-N.html, og:title
-- present, HTTP 200.
--
-- *** WHY IT STILL CANNOT BE ENABLED AS-IS ***
--   Ekantipur article pages carry NO <h1>. The headline is in
--   <h2>, with the structured data in a JSON-LD "headline" field.
--   verify_entries() requires an <h1>, so all 40 probed links
--   fail and the poll raises "40/40 candidate links did not
--   resolve". No selector_config can pass that check. Enabling
--   this source needs is_article() in app/sources/html_listing.py
--   to accept <h2>/og:title as well, first.
--
--   Its robots.txt contains only a content-signals preamble -- no
--   User-agent, no Disallow -- so robots does not object.
--
-- Run BOTH statements, and only after the is_article() change:
--
-- update sources
-- set selector_config = '{"item":"div.news-section:not(:has(.author-image))","link":"h2 a[href], h3 a[href]","title":"h2, h3","image":"figure img","image_attr":"data-src","date":"time","exclude":"/author/|/e-paper|/video|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf)$"}'::jsonb
-- where name = 'Ekantipur';
--
-- update sources set allowed = true where name = 'Ekantipur';


-- ---------------------------------------------------------
-- Himal Khabar  --  CANNOT BE SCRAPED, LEAVE IT OFF
--
-- The served HTML of https://www.himalkhabar.com/ contains no
-- article links at all: only nav, social, /page/*, /patrachar,
-- /unicode and /special. There is no sitemap (404), no
-- application/ld+json blob, and no listing or news API behind the
-- JS -- apps.js and the inline scripts reference only
-- /newsletter and /api/comment-*. The strings "news-container"
-- and "news-cat" appear in scripts, not in markup.
--
-- Nothing in selector_config changes a page that ships no
-- listings. This one stays disabled.
--
-- update sources set allowed = false where name = 'Himal Khabar';


-- ---------------------------------------------------------
-- DEAD DOMAINS -- NXDOMAIN, verified against Cloudflare DoH
--
-- These seven return DNS NXDOMAIN (RCODE 3) for the apex AND the
-- www host, both on the local resolver and on 1.1.1.1:
-- annapurbapost.com, arthikabhojan.com, bhabishaanepal.com,
-- nepalkantha.com, nepalbhas.com, newahale.com, prasutojournal.com.
-- The hostnames do not exist, so there is no listing page to
-- select from and no selector could be verified for any of them.
-- They are NOT in the fix list for that reason, and no enable
-- statement is offered -- enabling a source whose domain does not
-- resolve buys nothing but dns_failure noise in the error log.
--
-- If these outlets are being replaced by successors, register
-- the new domains as new sources rows rather than re-pointing
-- these.
--
-- update sources set enabled = false where name in (
--   'Annapurna Post', 'Arthikabhojan', 'Bhabishaanepal',
--   'Nepal Kantha', 'Nepalbhas', 'Newahale', 'Prasuto Journal'
-- );