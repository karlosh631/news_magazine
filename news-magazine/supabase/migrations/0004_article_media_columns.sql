-- Add media fields used by the ingestion worker and public article views.
-- Safe to apply to an existing production database.

alter table articles
  add column if not exists video_url text,
  add column if not exists audio_url text,
  add column if not exists gif_url text;

comment on column articles.video_url is 'Optional direct video or stream URL from permitted source metadata.';
comment on column articles.audio_url is 'Optional direct audio URL from permitted source metadata.';
comment on column articles.gif_url is 'Optional animated image URL from permitted source metadata.';