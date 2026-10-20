-- 0016_articles_image_verified_at.sql
--
-- Marks a row's artwork as having been resolved by the CURRENT
-- extraction rules.
--
-- WHY THIS IS NEEDED
-- ------------------
-- An article's image is computed exactly once, on the poll that first
-- stores it. `dedup.check` matches the row on every later sighting
-- and `continue`s long before the media block, so nothing ever
-- re-examines a stored row's image. That makes the image WRITE-ONCE.
--
-- The consequence is that fixing the extraction does not move the
-- rows already in the database. The ranking that now prefers the
-- outlet's own og:image over a photograph scraped out of the page
-- body applies to every NEW row and to no old one, so a story stored
-- under the old rules keeps its wrong picture for ever.
--
-- Observed in production: an article about 576,000 people leaving
-- Kathmandu Valley for Dashain showed a photograph of foliage, while
-- the outlet's own page shows the traffic jam. Both are valid images;
-- only one of them is this article's.
--
-- WHAT THIS COLUMN DOES
-- ----------------------
-- It is a one-shot marker, not a status. The repair pass picks rows
-- where it is NULL, re-resolves their artwork with today's rules, and
-- sets it. Each row is therefore fetched ONCE, ever, rather than
-- being re-fetched on every poll for ever — which is the difference
-- between a repair that completes and a repair that becomes a
-- permanent extra load on 27 publishers.
--
-- It is deliberately NOT written by the main store path. If this
-- migration has not been applied, every article insert would fail on
-- an unknown column and the whole news pipeline would stop. Instead
-- the repair pass is the only writer, and it tolerates the column
-- being absent: the query fails, the pass logs it and moves on, and
-- nothing else is affected.
--
-- Safe to run repeatedly. Additive, nullable, no constraint.

alter table articles
  add column if not exists image_verified_at timestamptz;

-- The repair pass selects by (column is null, within a window, this
-- source), and orders by arrival so the bounded batch walks the
-- window instead of re-doing the same rows.
create index if not exists articles_image_verified_idx
  on articles (image_verified_at, ingested_at)
  where image_verified_at is null;
