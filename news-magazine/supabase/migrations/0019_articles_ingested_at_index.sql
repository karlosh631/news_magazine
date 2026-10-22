-- =============================================================
-- Index the feed's real ordering
--
-- The articles feed is ordered by `ingested_at` — when WE first saw
-- the story — and not by `published_at`, when the outlet published
-- it. The reader is promised the most recently FETCHED story on top,
-- and the difference is not cosmetic: a publisher can sit on a story
-- for hours before it appears in their feed, and when it does appear
-- it is new to this site.
--
-- The query that fills the feed's window was still ordered by
-- `published_at`, so the newest-arrived story was only on the page if
-- it also happened to be among the newest published. A story
-- published five hours ago and fetched a minute ago fell outside the
-- window entirely and never appeared at all.
--
-- The code now orders by `ingested_at` and pages by it, and this
-- index is what makes that cheap. Without it, every poll - one every
-- ten seconds, per reader - sorts the whole published set. With it,
-- the query is an index scan.
--
-- The index is PARTIAL and it matches the query's predicate exactly:
-- the feed only ever reads published rows that are not a duplicate of
-- another story. A partial index on exactly the rows that are read is
-- smaller, and it stays correct as the table grows.
--
-- `nulls last` is written out rather than left to the default,
-- because PostgreSQL's default for `DESC` is `NULLS FIRST` and the
-- query asks for `nullsFirst: false`. An index whose null ordering
-- does not match the query's cannot be used for it.
--
-- Safe to apply at any time. The index is built without a lock that
-- blocks reads or writes on a table of this size.
-- =============================================================

-- Backfill the arrival time for rows that predate the column.
--
-- `articles.ingested_at` was added as a plain nullable column, so rows
-- written before it existed have no arrival time. A NULL arrival time
-- sorts LAST and, worse, is unreachable through the feed's keyset
-- cursor - the filter is `ingested_at.lt.X`, which never matches NULL.
-- Those stories would fall off the end of the archive entirely.
--
-- Their publication time is the best arrival time available: it is
-- when the story was written, which is within a short distance of when
-- it was first fetched for every row that was in the database before
-- the column existed.
update articles
set ingested_at = published_at
where ingested_at is null
  and published_at is not null
  and status = 'published';

create index if not exists articles_ingested_at_idx
  on articles (ingested_at desc nulls last, id desc)
  where status = 'published' and duplicate_of is null;

-- The reels feed is ordered the same way, and had the same problem: it
-- is ordered by `ingested_at` with `published_at` as the tiebreak, and
-- the only index behind it was on `published_at` alone.
--
-- The reels table's `ingested_at` is `not null default now()`, so the
-- null ordering does not matter here - but it is written out anyway, so
-- the index matches the query exactly and cannot drift from it.
create index if not exists reels_ingested_at_idx
  on reels (ingested_at desc nulls last, published_at desc nulls last);
