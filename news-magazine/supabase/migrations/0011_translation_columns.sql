-- =========================================================
-- 0011_translation_columns.sql
-- Machine-translated headline and summary, kept separate from the
-- outlet's original text.
--
-- The rule this migration encodes: a translation NEVER overwrites the
-- source. `headline` and `summary_points` remain exactly as the outlet
-- published them, because that is the reporting. The translation lives
-- here and is rendered only on request, labelled as machine-generated
-- and attributed to the translator rather than the outlet.
-- =========================================================

alter table articles
  add column if not exists headline_translated text,
  add column if not exists summary_points_translated jsonb,
  add column if not exists translation_language text,
  add column if not exists translated_by text,
  add column if not exists translated_at timestamptz;

comment on column articles.headline_translated is
  'Machine translation of headline. NEVER the canonical title: the original '
  'headline is what the outlet published and must stay authoritative.';

comment on column articles.summary_points_translated is
  'Machine translation of summary_points. Same rule — original wins.';

comment on column articles.translated_by is
  'Which model produced the translation, so the UI can label it rather than '
  'implying the outlet wrote it.';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'articles_summary_translated_shape'
  ) then
    alter table articles
      add constraint articles_summary_translated_shape
      check (
        summary_points_translated is null
        or jsonb_typeof(summary_points_translated) = 'array'
      ) not valid;
  end if;
end;
$$;

-- Only translated rows, for the "show translation" lookup.
create index if not exists articles_translated_idx
  on articles (translated_at desc)
  where headline_translated is not null;