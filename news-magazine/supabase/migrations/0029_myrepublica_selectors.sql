-- =============================================================
-- 0029_myrepublica_selectors.sql
-- Give MyRepublica a headline and a picture that belong to the story.
--
-- WHAT WAS WRONG
-- ---------------
-- The card on MyRepublica's listing page is an ANCHOR, and it holds:
--
--   <a href=".../news/floods-landslides-....html">
--     <div class="rep-body--x-small ...">25 minutes ago</div>
--     <div>
--       <span class="rep-misc__tag">SOCIETY</span>
--       <h3 class="mb-2 rep-title--large lg:block hidden">
--         Floods, landslides disrupt roads in Gandaki, Karnali
--       </h3>
--       <h3 class="mb-1 rep-title--small font-semibold block lg:hidden">
--         Floods, landslides disrupt roads in Gandaki, Karnali
--       </h3>
--     </div>
--     <figure class="rep-figure"><img src=".../Jomsom-....webp"></figure>
--   </a>
--
-- Two things about that card confuse a scraper:
--
--   1. THE TITLE IS IN AN h1 or an h3, and in the list layout there
--      are TWO of them - one for the desktop layout and one for the
--      phone layout - with the same words in each. The outlet uses
--      TWO class families for the headline depending on the layout:
--      `rep-headline--` on the big cards (an h1 and an h3) and
--      `rep-title--` on the small ones (an h3 and an h4). So the
--      headline has to be read out of those, not out of the card.
--
--      Note that the outlet also puts the story's NUMBER in a
--      `span.rep-headline--medium`. The selector is deliberately
--      `h1[class*=...]` and not `*[class*=...]`, so the number is
--      never mistaken for the headline.
--
--   2. THE CATEGORY TAG ("SOCIETY", "SPORTS", "WORLD", "POLITICS")
--      and the timestamp are in the card too, and the summary
--      paragraph repeats the headline.
--
-- The row had no `title` selector, so the adapter fell back to the
-- LINK'S OWN TEXT, which is the whole card. The stored headline was
-- therefore the category tag, the timestamp, the headline TWICE and
-- the summary - which is what the site was showing:
--
--   "SPORTS Nepal face Oman in crucial World Cup Lea... Nepal face
--    Oman in crucial World Cup Lea..."
--
-- The picture had the same problem: with no `image` selector the
-- adapter took the first <img> in the card, which is not always the
-- story's own.
--
-- And there is a second trap in the picture. Half the cards are
-- lazy-loaded:
--
--   <img data-src="https://.../TU-1792643588.webp"
--        src="https://.../img/default_image.png"
--        class="rep-image lazyload" />
--
-- The REAL picture is in `data-src` and the `src` is a grey
-- placeholder the site shows until the picture scrolls into view.
-- Reading `src` stores the placeholder - and a placeholder is a
-- picture that belongs to no story at all. So the card is read with
-- `image_attr: data-src`, and the adapter falls back to `src` for
-- the cards that are not lazy-loaded.
--
-- THE FIX
-- -------
-- `title` is the h3 that carries the outlet's own `rep-title` class,
-- and `image` is the figure's img. Both are inside the anchor, so
-- the adapter finds them as descendants of the card, and the card is
-- the link.
--
-- Measured 2026-10-22 against the live homepage: 80 cards, every
-- one of them with a headline in an h3 and a picture in a figure.
--
-- THE DATE
-- --------
-- The listing publishes "25 minutes ago" in a DIV, not in a <time>
-- element, and it has no other date in the card. So there is no
-- date to read and the row is stored with the time it was fetched.
-- The homepage's JSON-LD carries an exact `datePublished` for every
-- card, and reading that is the next improvement rather than a
-- change made here.
--
-- Idempotent: the row is updated, and updating it twice is the
-- same as updating it once.
-- =============================================================

update sources
set selector_config = '{
      "item": "a[href*=''/news/'']",
      "link": "a[href]",
      "title": "h1[class*=''rep-headline''], h2[class*=''rep-headline''], h3[class*=''rep-headline''], h3[class*=''rep-title''], h4[class*=''rep-title'']",
      "image": "figure img",
      "image_attr": "data-src",
      "date": "time",
      "exclude": "/category/|/tag/|/author/|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"
    }'::jsonb,
    notes = notes ||
      ' | 2026-10-22: the headline and the picture are now read out of the card
       instead of out of the anchor''s whole text. The card is an anchor holding a
       category tag, a timestamp, the headline TWICE (a rep-title--large for the
       desktop layout and a rep-title--small for the phone one), a summary that
       repeats the headline, and the picture in a figure. With no title selector
       the adapter fell back to the link''s text, so the stored headline was the
       tag and the timestamp and the headline twice and the summary. Measured
       against the live homepage: 80 cards, every one with an h3 carrying the
       rep-title class and a figure with the story''s own image.'
where name = 'MyRepublica (Nagarik Network)';
