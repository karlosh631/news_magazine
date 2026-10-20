-- =========================================================
-- 0015_reels_embed_url.sql
-- A second URL on reels, for providers that cannot offer a playable file.
--
-- Why this exists
-- ---------------
-- Every provider in migration 0014 hands the browser a directly playable
-- media file on its own CDN, and the web player is a plain
-- `<video src={reel.video_url}>`. That is the whole design and it still
-- is for Wikimedia, Pexels and Pixabay.
--
-- YouTube is the exception and is not a redistributable source like
-- those three. Its media lives behind a signature that has to be derived
-- from YouTube's own player, and decoding one is scraping. So the
-- YouTube adapter points `video_url` at the official embed player src
-- (`https://www.youtube.com/embed/<id>`) instead — which is an HTML
-- document, not a media file, and which a `<video>` element will refuse
-- to play.
--
-- Shipping that without this column would produce a feed where every
-- YouTube card renders "Replay unavailable". This column is what lets the
-- player choose <iframe> over <video>.
--
-- Why `video_url` is left alone
-- -----------------------------
-- It stays NOT NULL and keeps its meaning: "the URL a browser needs in
-- order to play this clip". For an embedded provider that is the player
-- src, which does play — inside an iframe. Making video_url nullable and
-- pointing it at a watch page instead would have broken every query,
-- filter and index that already assumes it is present.
--
-- This is still two URLs and zero bytes. Nothing here stores media, and
-- the rule in 0014 that nothing ever will is untouched.
--
-- Idempotent: safe to apply to an existing production database.
-- =========================================================

alter table reels add column if not exists embed_url text;

-- ---------------------------------------------------------
-- Why there is no constraint on it
-- ---------------------------------------------------------
-- Nullable, with no CHECK, on purpose. `null` is not "unknown" — it is
-- the affirmative statement "this clip is a plain media file", which is
-- every row written before this column existed and every row from the
-- three providers that still are. Backfilling them would be a large
-- write that changes nothing the player does.
--
-- ---------------------------------------------------------
-- Backfill
--
-- Existing youtube rows, if an operator ran the provider before applying
-- this migration, are stored with an embed player URL in video_url and a
-- NULL embed_url, so they render as unplayable. They are repaired rather
-- than deleted: the rows are correctly attributed and correctly
-- filtered, and only the player hint was missing.
--
-- The host check is the guard that keeps this from being a mass UPDATE
-- on the wrong column — video_url is `not null`, so `embed_url is null`
-- alone would otherwise match every row in the table.
-- ---------------------------------------------------------

update reels
   set embed_url = video_url
 where embed_url is null
   and source_key = 'youtube'
   and video_url like 'https://www.youtube.com/embed/%';

comment on column reels.embed_url is
  'Official provider embed-player src, set only when video_url is not a '
  'directly playable media file (currently the YouTube Shorts provider). '
  'Null means a plain media file. The web player renders an <iframe> when '
  'this is present and a <video> when it is not.';
