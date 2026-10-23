-- =========================================================
-- 0034_storage_pressure.sql
--
-- Let the worker ask the database how full it is.
--
-- WHY
-- ---
-- The owner asked for one thing in as many words: never let the
-- system collapse because the database is full, and do it on its own.
--
-- The guard that does it lives in
-- `apps/ingestion/app/services/storage_guard.py`. It prunes the log
-- tables by age on every pass, and when the database is at eighty
-- percent of its limit it deletes the oldest half of the articles.
--
-- To know when it is at eighty percent, it has to know how big the
-- database is. The database is the only thing that knows that:
-- `pg_database_size` is how Postgres answers.
--
-- Without this function the guard falls back to weighing the rows it
-- can count, which moves in the same direction but is a guess. With
-- it, the guard has the real number, taken from the database's own
-- mouth.
--
-- THE INDEXES
-- -----------
-- The guard deletes by timestamp, and these three tables are the ones
-- it deletes from. `source_sync_logs` grows by one row per outlet per
-- pass - thirty-four rows, three minutes apart - so it is the fastest
-- growing table in the database and the one most in need of an index
-- on the column the prune filters by.
--
-- Apply with:  supabase db push
-- =========================================================

create or replace function storage_pressure()
returns jsonb
language sql
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'used_bytes',      pg_database_size(current_database()),
    'article_rows',    (select count(*) from articles),
    'sync_log_rows',   (select count(*) from source_sync_logs),
    'error_rows',      (select count(*) from source_errors),
    'analytics_rows',  (select count(*) from analytics_events)
  );
$$;

comment on function storage_pressure() is
  'How full the database is, in bytes, and how many rows each of the '
  'tables that grow holds. Read by the storage guard '
  '(app/services/storage_guard.py) once per pass.';

-- The prunes filter on these three columns.
create index if not exists source_sync_logs_started_at_idx
  on source_sync_logs (started_at);

create index if not exists source_errors_occurred_at_idx
  on source_errors (occurred_at);

create index if not exists analytics_events_occurred_at_idx
  on analytics_events (occurred_at);

-- ---------------------------------------------------------
-- Verify after applying
--
--   select storage_pressure();
--
-- Expect a jsonb object with used_bytes, article_rows, sync_log_rows,
-- error_rows and analytics_rows.
--
-- The used_bytes number is what the guard compares with the plan's
-- limit (500 MB on the free tier, set by
-- DATABASE_SIZE_LIMIT_BYTES). If it is over eighty percent of that,
-- the next pass deletes the oldest half of the articles and says so
-- in the run log.
-- ---------------------------------------------------------
