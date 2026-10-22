-- =============================================================
-- The Rashifal category
--
-- A Nepali reader checks the almanac before they read the news, and
-- the site had nowhere to put it. The daily panchang is fetched and
-- stored by the ingestion worker (migration 0022), and this adds the
-- category the page hangs from.
--
-- WHY A CATEGORY ROW AND NOT A ROUTE
-- ----------------------------------
-- The navigation is built from the `categories` table, so a page
-- that is not a category is not in the navigation. And a reader who
-- has bookmarked "/category/rashifal" expects that URL to work the
-- same way as every other category URL.
--
-- The row carries no articles, and that is fine: the category is a
-- VIEW onto the almanac, not a bucket of stories. The `categories`
-- table is what the nav and the routing are built from, and the
-- articles table carries its own `primary_category_id`, so nothing
-- here claims that any article belongs to this.
--
-- `on conflict (slug) do nothing` because the slug is unique and
-- re-running the migration must not fail.
-- =============================================================

insert into categories (slug, name_en, name_ne, sort_order)
values ('rashifal', 'Rashifal', 'राशिफल', 8)
on conflict (slug) do nothing;
